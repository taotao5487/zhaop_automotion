import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from crawler.models.job import Base, MedicalJob, CrawlStatus, ReviewDecision
from shared.paths import DATA_DIR

logger = logging.getLogger(__name__)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value in {None, ""}:
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def _normalize_job_datetimes(job_data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(job_data)
    for field_name in ("publish_date", "crawl_time"):
        if field_name in normalized:
            normalized[field_name] = _coerce_datetime(normalized.get(field_name))
    return normalized


def _needs_publish_date_nullable_migration(db_path: str) -> bool:
    try:
        connection = sqlite3.connect(db_path)
        try:
            columns = list(connection.execute("PRAGMA table_info(medical_jobs)"))
        finally:
            connection.close()
    except sqlite3.Error:
        return False

    for column in columns:
        if len(column) >= 4 and column[1] == "publish_date":
            return bool(column[3])
    return False


def _migrate_publish_date_nullable(db_path: str) -> bool:
    if not _needs_publish_date_nullable_migration(db_path):
        return False

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("BEGIN")
        connection.execute(
            """
            CREATE TABLE medical_jobs__codex_migrate (
                id INTEGER PRIMARY KEY,
                title VARCHAR NOT NULL,
                url VARCHAR NOT NULL UNIQUE,
                publish_date DATETIME,
                hospital VARCHAR,
                location VARCHAR,
                source_site VARCHAR,
                crawl_time DATETIME,
                is_new BOOLEAN
            )
            """
        )
        connection.execute(
            """
            INSERT INTO medical_jobs__codex_migrate
            (id, title, url, publish_date, hospital, location, source_site, crawl_time, is_new)
            SELECT id, title, url, publish_date, hospital, location, source_site, crawl_time, is_new
            FROM medical_jobs
            """
        )
        connection.execute("DROP TABLE medical_jobs")
        connection.execute("ALTER TABLE medical_jobs__codex_migrate RENAME TO medical_jobs")
        connection.commit()
        return True
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


class DatabaseManager:
    """数据库管理器"""

    def __init__(self, db_path: str = str(DATA_DIR / "jobs.db"), use_async: bool = True):
        self.db_path = db_path
        self.use_async = use_async
        self.engine = None
        self.async_engine = None
        self.Session = None
        self.AsyncSession = None

    def init_sync(self):
        """初始化同步数据库连接"""
        self.engine = create_engine(f"sqlite:///{self.db_path}", echo=False)
        Base.metadata.create_all(self.engine)
        migrated = _migrate_publish_date_nullable(self.db_path)
        if migrated:
            logger.info("数据库迁移完成: medical_jobs.publish_date 已允许为空")
        self.Session = sessionmaker(bind=self.engine)
        logger.info(f"同步数据库初始化完成: {self.db_path}")

    async def init_async(self):
        """初始化异步数据库连接"""
        self.async_engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path}",
            echo=False,
            connect_args={"timeout": 30},
        )
        async with self.async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        migrated = _migrate_publish_date_nullable(self.db_path)
        if migrated:
            logger.info("数据库迁移完成: medical_jobs.publish_date 已允许为空")
        self.AsyncSession = async_sessionmaker(
            bind=self.async_engine,
            expire_on_commit=False
        )
        logger.info(f"异步数据库初始化完成: {self.db_path}")

    def get_sync_session(self) -> Session:
        """获取同步会话"""
        if not self.Session:
            self.init_sync()
        return self.Session()

    async def get_async_session(self) -> AsyncSession:
        """获取异步会话"""
        if not self.AsyncSession:
            await self.init_async()
        return self.AsyncSession()

    async def save_job(self, job_data: Dict[str, Any]) -> Optional[MedicalJob]:
        """保存招聘职位（异步）"""
        saved_job, _ = await self.save_or_update_job(job_data, update_existing=True)
        return saved_job

    async def save_or_update_job(
        self,
        job_data: Dict[str, Any],
        update_existing: bool = True,
    ) -> Tuple[Optional[MedicalJob], bool]:
        """保存或更新招聘职位，返回(职位对象, 是否新插入)"""
        async with await self.get_async_session() as session:
            try:
                normalized_job_data = _normalize_job_datetimes(job_data)
                # 检查是否已存在
                stmt = select(MedicalJob).where(MedicalJob.url == normalized_job_data['url'])
                result = await session.execute(stmt)
                existing_job = result.scalar_one_or_none()

                if existing_job:
                    if not update_existing:
                        return existing_job, False

                    # 更新现有记录
                    for key, value in normalized_job_data.items():
                        if hasattr(existing_job, key):
                            setattr(existing_job, key, value)
                    existing_job.crawl_time = datetime.now()
                    existing_job.is_new = False
                else:
                    # 创建新记录
                    new_job = MedicalJob(**normalized_job_data)
                    new_job.crawl_time = datetime.now()
                    new_job.is_new = True
                    session.add(new_job)
                    existing_job = new_job

                await session.commit()
                logger.debug(f"职位保存成功: {normalized_job_data.get('title', 'Unknown')}")
                return existing_job, existing_job.is_new is True

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"保存职位失败: {e}, 数据: {job_data}")
                return None, False

    async def save_jobs_batch(self, jobs_data: List[Dict[str, Any]]) -> int:
        """批量保存招聘职位（异步）"""
        saved_count = 0
        for job_data in jobs_data:
            result = await self.save_job(job_data)
            if result:
                saved_count += 1
        logger.info(f"批量保存完成: {saved_count}/{len(jobs_data)} 个职位")
        return saved_count

    async def get_recent_jobs(self,
                            days: int = 30,
                            site_name: Optional[str] = None,
                            limit: int = 100) -> List[MedicalJob]:
        """获取最近N天的招聘职位（异步）"""
        async with await self.get_async_session() as session:
            try:
                cutoff_date = datetime.now() - timedelta(days=days)
                stmt = select(MedicalJob).where(
                    MedicalJob.publish_date >= cutoff_date
                ).order_by(MedicalJob.publish_date.desc())

                if site_name:
                    stmt = stmt.where(MedicalJob.source_site == site_name)

                if limit:
                    stmt = stmt.limit(limit)

                result = await session.execute(stmt)
                jobs = result.scalars().all()
                logger.debug(f"获取到 {len(jobs)} 个最近职位")
                return jobs

            except SQLAlchemyError as e:
                logger.error(f"获取最近职位失败: {e}")
                return []

    async def update_crawl_status(self,
                                site_name: str,
                                status: str = "success",
                                error_message: Optional[str] = None,
                                new_jobs: int = 0) -> bool:
        """更新爬取状态（异步）"""
        async with await self.get_async_session() as session:
            try:
                stmt = select(CrawlStatus).where(CrawlStatus.site_name == site_name)
                result = await session.execute(stmt)
                crawl_status = result.scalar_one_or_none()

                if not crawl_status:
                    crawl_status = CrawlStatus(site_name=site_name)
                    session.add(crawl_status)

                crawl_status.last_crawl_time = datetime.now()
                crawl_status.status = status
                crawl_status.error_message = error_message

                if status == "success":
                    # 成功时更新统计
                    if crawl_status.total_crawled is None:
                        crawl_status.total_crawled = 0
                    crawl_status.total_crawled += new_jobs
                    crawl_status.new_jobs = new_jobs

                await session.commit()
                logger.info(f"更新爬取状态: {site_name} -> {status}")
                return True

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"更新爬取状态失败: {e}")
                return False

    async def get_last_crawl_time(self, site_name: str) -> Optional[datetime]:
        """获取上次爬取时间（异步）"""
        async with await self.get_async_session() as session:
            try:
                stmt = select(CrawlStatus).where(CrawlStatus.site_name == site_name)
                result = await session.execute(stmt)
                crawl_status = result.scalar_one_or_none()

                if crawl_status and crawl_status.last_crawl_time:
                    return crawl_status.last_crawl_time
                return None

            except SQLAlchemyError as e:
                logger.error(f"获取上次爬取时间失败: {e}")
                return None

    async def job_exists(self, url: str) -> bool:
        """检查职位是否已存在（异步）"""
        async with await self.get_async_session() as session:
            try:
                stmt = select(MedicalJob).where(MedicalJob.url == url)
                result = await session.execute(stmt)
                return result.scalar_one_or_none() is not None
            except SQLAlchemyError as e:
                logger.error(f"检查职位存在性失败: {e}")
                return False

    async def get_jobs_count(self, site_name: Optional[str] = None) -> int:
        """获取职位总数（异步）"""
        async with await self.get_async_session() as session:
            try:
                stmt = select(MedicalJob)
                if site_name:
                    stmt = stmt.where(MedicalJob.source_site == site_name)
                result = await session.execute(stmt)
                return len(result.scalars().all())
            except SQLAlchemyError as e:
                logger.error(f"获取职位总数失败: {e}")
                return 0

    async def get_review_decisions(self, urls: List[str]) -> Dict[str, ReviewDecision]:
        """按URL批量获取审核决策"""
        if not urls:
            return {}

        async with await self.get_async_session() as session:
            try:
                stmt = select(ReviewDecision).where(ReviewDecision.url.in_(urls))
                result = await session.execute(stmt)
                decisions = result.scalars().all()
                return {item.url: item for item in decisions}
            except SQLAlchemyError as e:
                logger.error(f"获取审核决策失败: {e}")
                return {}

    async def touch_review_decisions(self, jobs_data: List[Dict[str, Any]]) -> int:
        """更新已存在审核决策的最近看到时间与快照"""
        if not jobs_data:
            return 0

        async with await self.get_async_session() as session:
            try:
                urls = [str(job.get("url") or "").strip() for job in jobs_data if str(job.get("url") or "").strip()]
                if not urls:
                    return 0

                stmt = select(ReviewDecision).where(ReviewDecision.url.in_(urls))
                result = await session.execute(stmt)
                existing = {item.url: item for item in result.scalars().all()}
                seen_time = datetime.now()
                updated = 0

                for job in jobs_data:
                    url = str(job.get("url") or "").strip()
                    if not url or url not in existing:
                        continue

                    decision = existing[url]
                    decision.last_seen_at = seen_time
                    decision.source_site = job.get("source_site")
                    decision.title_snapshot = job.get("title")
                    decision.hospital_snapshot = job.get("hospital")
                    decision.publish_date_snapshot = _coerce_datetime(job.get("publish_date"))
                    updated += 1

                if updated:
                    await session.commit()
                return updated

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"更新审核决策最近看到时间失败: {e}")
                return 0

    async def save_review_decisions(self, jobs_with_decision: List[Dict[str, Any]]) -> int:
        """保存详情审核决策，支持覆盖已有决策"""
        if not jobs_with_decision:
            return 0

        async with await self.get_async_session() as session:
            try:
                urls = [
                    str(item.get("url") or "").strip()
                    for item in jobs_with_decision
                    if str(item.get("url") or "").strip()
                ]
                if not urls:
                    return 0

                stmt = select(ReviewDecision).where(ReviewDecision.url.in_(urls))
                result = await session.execute(stmt)
                existing = {item.url: item for item in result.scalars().all()}
                now = datetime.now()
                saved_count = 0

                for item in jobs_with_decision:
                    url = str(item.get("url") or "").strip()
                    decision_value = str(item.get("decision") or "").strip()
                    if not url or decision_value not in {"keep", "discard"}:
                        continue

                    row = existing.get(url)
                    if row is None:
                        row = ReviewDecision(url=url)
                        session.add(row)
                        existing[url] = row

                    row.decision = decision_value
                    row.decided_at = now
                    row.last_seen_at = now
                    row.source_site = item.get("source_site")
                    row.title_snapshot = item.get("title")
                    row.hospital_snapshot = item.get("hospital")
                    row.publish_date_snapshot = _coerce_datetime(item.get("publish_date"))
                    saved_count += 1

                if saved_count:
                    await session.commit()
                return saved_count

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"保存审核决策失败: {e}")
                return 0

    async def cleanup_old_jobs(self, days: int = 180) -> int:
        """清理指定天数之前的旧职位（异步）"""
        async with await self.get_async_session() as session:
            try:
                cutoff_date = datetime.now() - timedelta(days=days)
                stmt = select(MedicalJob).where(MedicalJob.publish_date < cutoff_date)
                result = await session.execute(stmt)
                old_jobs = result.scalars().all()

                deleted_count = 0
                for job in old_jobs:
                    await session.delete(job)
                    deleted_count += 1

                await session.commit()
                logger.info(f"清理了 {deleted_count} 个 {days} 天前的旧职位")
                return deleted_count

            except SQLAlchemyError as e:
                await session.rollback()
                logger.error(f"清理旧职位失败: {e}")
                return 0


# 全局数据库管理器实例
db_manager = DatabaseManager()
