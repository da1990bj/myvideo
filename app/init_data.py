from sqlmodel import Session, select
from database import engine, get_session
from data_models import Category

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

if __name__ == "__main__":
    init_categories()
