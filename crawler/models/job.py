from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class MedicalJob(Base):
    """医疗招聘职位数据模型"""
    __tablename__ = 'medical_jobs'

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    url = Column(String, unique=True, nullable=False)
    publish_date = Column(DateTime, nullable=True)
    hospital = Column(String)
    location = Column(String)
    source_site = Column(String)
    crawl_time = Column(DateTime, default=datetime.now)
    is_new = Column(Boolean, default=True)

    def __repr__(self):
        return f"<MedicalJob(title='{self.title}', hospital='{self.hospital}', publish_date='{self.publish_date}')>"

    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'title': self.title,
            'url': self.url,
            'publish_date': self.publish_date.isoformat() if self.publish_date else None,
            'hospital': self.hospital,
            'location': self.location,
            'source_site': self.source_site,
            'crawl_time': self.crawl_time.isoformat() if self.crawl_time else None,
            'is_new': self.is_new
        }


class ReviewDecision(Base):
    """详情审核决策缓存"""
    __tablename__ = 'review_decisions'

    url = Column(String, primary_key=True)
    decision = Column(String, nullable=False)  # 'keep' | 'discard'
    decided_at = Column(DateTime, nullable=False, default=datetime.now)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.now)
    source_site = Column(String)
    title_snapshot = Column(String)
    hospital_snapshot = Column(String)
    publish_date_snapshot = Column(DateTime)

    def __repr__(self):
        return f"<ReviewDecision(url='{self.url}', decision='{self.decision}', decided_at='{self.decided_at}')>"


class CrawlStatus(Base):
    """爬取状态数据模型"""
    __tablename__ = 'crawl_status'

    site_name = Column(String, primary_key=True)
    last_crawl_time = Column(DateTime)
    status = Column(String)  # 'success', 'failed', 'pending'
    error_message = Column(String)
    total_crawled = Column(Integer, default=0)
    new_jobs = Column(Integer, default=0)

    def __repr__(self):
        return f"<CrawlStatus(site='{self.site_name}', last_crawl='{self.last_crawl_time}', status='{self.status}')>"
