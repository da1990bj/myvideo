"""
正剧相关路由（电影/电视剧/动漫）
"""
from typing import List, Optional, Tuple
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from sqlalchemy import and_, or_

from database import get_session
from data_models import Video, VideoRead, Category, User, UserRole, Role, DramaSeries, DramaSeriesItem
from dependencies import get_current_user, get_current_user_optional, PermissionChecker, check_drama_upload_permission

router = APIRouter(prefix="", tags=["正剧"])


def parse_multi_param(value: Optional[str]) -> Optional[List[str]]:
    """解析逗号分隔的多选参数"""
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]

# 正剧分类的 category slug
DRAMA_CATEGORY_SLUGS = ["movie", "tv", "anime"]

# 允许的正剧类型到 category slug 的映射
DRAMA_TYPE_MAP = {
    "movie": "movie",
    "tv": "tv",
    "anime": "anime"
}


def is_drama_category_slug(slug: str) -> bool:
    """检查 slug 是否为正剧分类"""
    return slug in DRAMA_CATEGORY_SLUGS


def get_category_by_slug(session: Session, slug: str) -> Optional[Category]:
    """通过 slug 获取分类"""
    return session.exec(select(Category).where(Category.slug == slug)).first()


def parse_year_range(year_str: str) -> Optional[Tuple[datetime, datetime]]:
    """解析年份范围字符串，返回 (start, end) datetime 元组"""
    from datetime import datetime

    year_ranges = {
        "2026": (datetime(2026, 1, 1), datetime(2027, 1, 1)),
        "2025": (datetime(2025, 1, 1), datetime(2026, 1, 1)),
        "2024": (datetime(2024, 1, 1), datetime(2025, 1, 1)),
        "2023": (datetime(2023, 1, 1), datetime(2024, 1, 1)),
        "2022": (datetime(2022, 1, 1), datetime(2023, 1, 1)),
        "2021": (datetime(2021, 1, 1), datetime(2022, 1, 1)),
        "2020": (datetime(2020, 1, 1), datetime(2021, 1, 1)),
        "2019": (datetime(2019, 1, 1), datetime(2020, 1, 1)),
        "2018": (datetime(2018, 1, 1), datetime(2019, 1, 1)),
        "2017": (datetime(2017, 1, 1), datetime(2018, 1, 1)),
        "2016": (datetime(2016, 1, 1), datetime(2017, 1, 1)),
        "2015": (datetime(2015, 1, 1), datetime(2016, 1, 1)),
        "2010-2014": (datetime(2010, 1, 1), datetime(2015, 1, 1)),
        "2005-2009": (datetime(2005, 1, 1), datetime(2010, 1, 1)),
        "2004-2000": (datetime(2000, 1, 1), datetime(2004, 1, 1)),
        "90年代": (datetime(1990, 1, 1), datetime(2000, 1, 1)),
        "80年代": (datetime(1980, 1, 1), datetime(1990, 1, 1)),
        "更早": (datetime(1900, 1, 1), datetime(1980, 1, 1)),
    }

    return year_ranges.get(year_str)


@router.get("/dramas/{drama_type}", response_model=List[VideoRead])
async def get_dramas(
    drama_type: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_optional),
    region: Optional[str] = Query(None, description="地区筛选"),
    language: Optional[str] = Query(None, description="语言筛选"),
    style: Optional[str] = Query(None, description="风格筛选"),
    kind: Optional[str] = Query(None, description="类型筛选：番剧、剧场版、电影"),
    year: Optional[str] = Query(None, description="年份筛选"),
    sort_by: str = Query("latest", enum=["latest", "popular", "rating"]),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    """
    获取正剧列表（支持多维筛选）

    - drama_type: movie(电影), tv(电视剧), anime(动漫)
    - region: 地区（如 "中国大陆", "日本", "美国"）
    - language: 语言（如 "汉语普通话", "日语", "英语"）
    - style: 风格/题材（如 "动作", "喜剧", "悬疑"）
    - kind: 类型（如 "番剧", "剧场版", "电影"）
    - year: 年份
    - sort_by: latest(最新), popular(最热), rating(评分)
    """
    if drama_type not in DRAMA_TYPE_MAP:
        raise HTTPException(status_code=400, detail="无效的正剧类型")

    offset = (page - 1) * size

    # 构建 DramaSeries 查询（正剧元数据在 DramaSeries 中）
    series_query = select(DramaSeries).where(
        DramaSeries.drama_type == drama_type,
        DramaSeries.is_public == True
    )

    # 地区筛选
    if region:
        regions = parse_multi_param(region)
        if regions:
            for r in regions:
                series_query = series_query.where(DramaSeries.drama_region.contains(r))

    # 语言筛选
    if language:
        languages = parse_multi_param(language)
        if languages:
            for l in languages:
                series_query = series_query.where(DramaSeries.drama_language.contains(l))

    # 风格筛选（JSON数组）
    if style:
        styles = parse_multi_param(style)
        if styles:
            for s in styles:
                series_query = series_query.where(DramaSeries.drama_style.contains(s))

    # 类型筛选（番剧/剧场版/电影）
    if kind:
        series_query = series_query.where(DramaSeries.drama_kind == kind)

    # 年份筛选
    if year and year != "全部":
        year_range = parse_year_range(year)
        if year_range:
            series_query = series_query.where(
                and_(
                    DramaSeries.drama_year >= year_range[0].year,
                    DramaSeries.drama_year < year_range[1].year
                )
            )
        else:
            try:
                specific_year = int(year)
                series_query = series_query.where(DramaSeries.drama_year == specific_year)
            except ValueError:
                pass

    # 排序
    if sort_by == "popular":
        series_query = series_query.order_by(DramaSeries.view_count.desc())
    elif sort_by == "rating":
        series_query = series_query.order_by(DramaSeries.rating.desc())
    else:  # latest
        series_query = series_query.order_by(DramaSeries.created_at.desc())

    series_query = series_query.offset(offset).limit(size)
    series_list = session.exec(series_query).all()

    # 获取剧集系列下的视频
    videos = []
    for series in series_list:
        items = session.exec(
            select(DramaSeriesItem)
            .where(DramaSeriesItem.series_id == series.id)
            .order_by(DramaSeriesItem.order)
        ).all()

        for item in items:
            video = session.get(Video, item.video_id)
            if video and not video.is_deleted and video.status == "completed":
                if video.is_approved == "approved" or video.visibility == "public":
                    videos.append(video)

    return videos


@router.get("/dramas/detail/{video_id}", response_model=VideoRead)
async def get_drama_detail(
    video_id: UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user_optional)
):
    """
    获取正剧详情
    """
    video = session.get(Video, video_id)
    if not video or video.is_deleted:
        raise HTTPException(status_code=404, detail="视频不存在")

    # 检查是否为正剧
    if not video.is_drama:
        raise HTTPException(status_code=400, detail="该视频不是正剧")

    # 审核状态检查
    if video.is_approved != "approved" and video.visibility != "public":
        if not current_user or (current_user.id != video.user_id and not current_user.is_admin):
            raise HTTPException(status_code=403, detail="无权访问该视频")

    return video


@router.put("/dramas/{video_id}", response_model=VideoRead)
async def update_drama(
    video_id: UUID,
    region: Optional[str] = None,
    language: Optional[str] = None,
    style: Optional[str] = None,
    total_episodes: Optional[int] = None,
    current_user: User = Depends(PermissionChecker("video:upload")),
    session: Session = Depends(get_session)
):
    """
    更新正剧元数据（仅管理员和运营人员）
    """
    video = session.get(Video, video_id)
    if not video or video.is_deleted:
        raise HTTPException(status_code=404, detail="视频不存在")

    if not video.is_drama:
        raise HTTPException(status_code=400, detail="该视频不是正剧")

    # 更新正剧属性
    if region is not None:
        video.drama_region = region
    if language is not None:
        video.drama_language = language
    if style is not None:
        video.drama_style = style
    if total_episodes is not None:
        video.total_episodes = total_episodes

    session.add(video)
    session.commit()
    session.refresh(video)

    return video


@router.get("/dramas/filters/{drama_type}")
async def get_drama_filters(
    drama_type: str,
    session: Session = Depends(get_session)
):
    """
    获取正剧筛选选项（从 FilterTab + DramaFilterOption 表获取，按 Tab 排序）
    """
    if drama_type not in DRAMA_TYPE_MAP:
        raise HTTPException(status_code=400, detail="无效的正剧类型")

    from data_models import DramaFilterOption, FilterTab
    import json

    drama_type_value = drama_type

    # 用于按 Tab 顺序存储选项
    filter_options_by_tab = {}  # { tab_slug: { "name": tab_name, "options": [] } }

    # 1. 获取所有启用的 Tab，按 display_order 排序
    tabs = session.exec(
        select(FilterTab).where(
            FilterTab.is_active == True
        ).order_by(FilterTab.display_order)
    ).all()

    # 初始化每个 Tab 的选项列表
    for tab in tabs:
        filter_options_by_tab[tab.slug] = {
            "name": tab.name,
            "slug": tab.slug,
            "options": []
        }

    # 2. 查询预定义选项，按 display_order 排序
    predefined_options = session.exec(
        select(DramaFilterOption).where(
            DramaFilterOption.is_active == True
        ).order_by(DramaFilterOption.display_order)
    ).all()

    for opt in predefined_options:
        if opt.tab_slug not in filter_options_by_tab:
            continue
        drama_types = json.loads(opt.drama_types) if opt.drama_types else None
        # 如果 drama_types 为空，表示适用于所有类型
        if drama_types is None or drama_type_value in drama_types:
            filter_options_by_tab[opt.tab_slug]["options"].append(opt.value)

    # 3. 不再从已有视频聚合筛选值，只显示分类管理中配置的值

    # 动态生成年份选项
    from datetime import datetime
    current_year = datetime.now().year
    year_options = ["全部"]

    # 近10年：具体年份（2016-当前年，共11年）- 始终显示所有选项
    for y in range(current_year, current_year - 11, -1):
        year_options.append(str(y))

    # 超过10年的：始终显示所有范围选项
    year_options.append("2010-2014")
    year_options.append("2005-2009")
    year_options.append("2000-2004")
    year_options.append("90年代")
    year_options.append("80年代")
    year_options.append("更早")

    # 构建返回结果，按 Tab 顺序
    tabs_data = []
    for tab in tabs:
        tab_data = filter_options_by_tab.get(tab.slug, {"name": tab.name, "slug": tab.slug, "options": []})
        tabs_data.append({
            "slug": tab.slug,
            "name": tab.name,
            "filter_type": tab.filter_type or "multi",
            "options": tab_data["options"]
        })

    return {
        "tabs": tabs_data,
        "years": year_options
    }
