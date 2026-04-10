from sqlmodel import Session, select
from database import engine, get_session
from data_models import Category, RecommendationSlot, SystemConfig

def init_categories():
    """初始化默认分类"""
    with Session(engine) as session:
        # 检查是否已有分类
        existing = session.exec(select(Category)).first()
        if existing:
            print("Categories already initialized.")
            return

        default_categories = [
            Category(name="科技", slug="tech"),
            Category(name="生活", slug="life"),
            Category(name="游戏", slug="game"),
            Category(name="影视", slug="movie"),
            Category(name="音乐", slug="music"),
            Category(name="动漫", slug="anime"),
        ]

        session.add_all(default_categories)
        session.commit()
        print(f"Initialized {len(default_categories)} categories.")

def init_recommendation_slots():
    """初始化默认推荐位"""
    with Session(engine) as session:
        # 检查是否已有推荐位
        existing = session.exec(select(RecommendationSlot)).first()
        if existing:
            print("Recommendation slots already initialized.")
            return

        default_slots = [
            RecommendationSlot(
                slot_name="home_carousel",
                display_title="首页精选轮播",
                description="首页顶部的轮播推荐位",
                max_items=5,
                recommendation_strategy="manual_first",
                show_authenticated=True,
                show_unauthenticated=True,
                unauthenticated_strategy="trending_only"
            ),
            RecommendationSlot(
                slot_name="sidebar_related",
                display_title="相关推荐",
                description="视频详情页侧边栏推荐",
                max_items=10,
                recommendation_strategy="mixed",
                show_authenticated=True,
                show_unauthenticated=True,
                unauthenticated_strategy="trending_only"
            ),
            RecommendationSlot(
                slot_name="category_featured",
                display_title="分类精选",
                description="各分类下的精选推荐",
                max_items=10,
                recommendation_strategy="manual_first",
                show_authenticated=True,
                show_unauthenticated=True,
                unauthenticated_strategy="trending_only"
            ),
            RecommendationSlot(
                slot_name="trending",
                display_title="热门推荐",
                description="最近热门的视频推荐",
                max_items=10,
                recommendation_strategy="algorithm_only",
                show_authenticated=True,
                show_unauthenticated=True,
                unauthenticated_strategy="trending_only"
            ),
            RecommendationSlot(
                slot_name="personalized",
                display_title="为你推荐",
                description="个性化推荐",
                max_items=20,
                recommendation_strategy="mixed",
                show_authenticated=True,
                show_unauthenticated=False,
                unauthenticated_strategy="hidden"
            ),
        ]

        session.add_all(default_slots)
        session.commit()
        print(f"Initialized {len(default_slots)} recommendation slots.")

def init_recommendation_config():
    """初始化推荐系统配置"""
    with Session(engine) as session:
        # 检查是否已有配置
        existing = session.exec(
            select(SystemConfig).where(SystemConfig.key == "recommendation_weight_collaborative")
        ).first()
        if existing:
            print("Recommendation config already initialized.")
            return

        # 初始化权重配置
        configs = [
            SystemConfig(
                key="recommendation_weight_collaborative",
                value="0.40",
                description="协同过滤权重 (0-1, 默认0.40)"
            ),
            SystemConfig(
                key="recommendation_weight_trending",
                value="0.30",
                description="热门内容权重 (0-1, 默认0.30)"
            ),
            SystemConfig(
                key="recommendation_weight_category",
                value="0.20",
                description="分类相似权重 (0-1, 默认0.20)"
            ),
            SystemConfig(
                key="recommendation_weight_tag",
                value="0.10",
                description="标签相似权重 (0-1, 默认0.10)"
            ),
            SystemConfig(
                key="recommendation_cache_ttl",
                value="86400",
                description="推荐缓存有效期（秒，默认86400=1天）"
            ),
            SystemConfig(
                key="recommendation_top_k",
                value="100",
                description="每用户保存的推荐视频数量（默认100）"
            ),
        ]

        session.add_all(configs)
        session.commit()
        print(f"Initialized {len(configs)} recommendation configs.")

def init_all_data():
    """初始化所有默认数据"""
    print("Initializing default data...")
    init_categories()
    init_recommendation_slots()
    init_recommendation_config()
    print("✅ All default data initialized!")

if __name__ == "__main__":
    init_all_data()
