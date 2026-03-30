import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


EXPORT_FIELDS = [
    'id',
    'title',
    'url',
    'hospital',
    'location',
    'source_site',
    'publish_date',
    'crawl_time',
    'is_new',
]


def _format_value(value: Any) -> str:
    """将导出值转换为稳定的字符串表示"""
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


def _normalize_job(job: Dict[str, Any]) -> Dict[str, str]:
    """规范化单条职位数据，便于导出"""
    normalized = {field: _format_value(job.get(field)) for field in EXPORT_FIELDS}
    normalized['hospital'] = normalized['hospital'] or '未知医院'
    return normalized


def export_new_jobs(
    jobs_data: List[Dict[str, Any]],
    export_directory: str,
    run_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """导出本次新增职位到 CSV 和 Markdown 清单"""
    if not jobs_data:
        return {'exported': False, 'reason': 'no_new_jobs'}

    normalized_jobs = [_normalize_job(job) for job in jobs_data]
    normalized_jobs.sort(key=lambda item: (item['hospital'], item['title'], item['url']))

    effective_run_time = run_time or datetime.now()
    timestamp = effective_run_time.strftime('%Y%m%d_%H%M%S')
    export_dir = Path(export_directory)
    export_dir.mkdir(parents=True, exist_ok=True)

    csv_path = export_dir / f"new_jobs_{timestamp}.csv"
    markdown_path = export_dir / f"wechat_digest_{timestamp}.md"

    with csv_path.open('w', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(normalized_jobs)

    grouped_jobs: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for job in normalized_jobs:
        grouped_jobs[job['hospital']].append(job)

    with markdown_path.open('w', encoding='utf-8') as markdown_file:
        markdown_file.write("# 本次新增招聘清单\n\n")
        markdown_file.write(f"- 运行时间：{effective_run_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        markdown_file.write(f"- 本次新增总数：{len(normalized_jobs)}\n\n")

        for hospital, hospital_jobs in grouped_jobs.items():
            markdown_file.write(f"## {hospital}\n\n")
            for index, job in enumerate(hospital_jobs, start=1):
                markdown_file.write(f"{index}. {job['title']}\n")
                markdown_file.write(f"   链接：{job['url']}\n")
            markdown_file.write("\n")

    return {
        'exported': True,
        'count': len(normalized_jobs),
        'csv_path': str(csv_path),
        'markdown_path': str(markdown_path),
    }
