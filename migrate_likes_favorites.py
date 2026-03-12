import os
from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://myvideo:myvideo_password@localhost:5432/myvideo_db"
engine = create_engine(DATABASE_URL)

def migrate_likes_favorites():
    print("Starting Likes & Favorites Migration...")

    # 1. Add like_count to videos
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE videos ADD COLUMN like_count INTEGER DEFAULT 0"))
            print("Added like_count to videos.")
    except Exception as e:
        print(f"Skipping like_count (maybe exists): {e}")

    # 1.1 Add favorite_count to videos
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE videos ADD COLUMN favorite_count INTEGER DEFAULT 0"))
            print("Added favorite_count to videos.")
    except Exception as e:
        print(f"Skipping favorite_count (maybe exists): {e}")

    # 1.2 Add favorite_count to collections
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE collections ADD COLUMN favorite_count INTEGER DEFAULT 0"))
            print("Added favorite_count to collections.")
    except Exception as e:
        print(f"Skipping favorite_count (maybe exists): {e}")

    # 2. Create video_favorites table
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS video_favorites (
                    user_id UUID NOT NULL,
                    video_id UUID NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    PRIMARY KEY (user_id, video_id),
                    FOREIGN KEY(user_id) REFERENCES users (id),
                    FOREIGN KEY(video_id) REFERENCES videos (id)
                )
            """))
            print("Created video_favorites table.")
    except Exception as e:
        print(f"Error creating video_favorites table: {e}")

    # 3. Create collection_favorites table
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS collection_favorites (
                    user_id UUID NOT NULL,
                    collection_id UUID NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    PRIMARY KEY (user_id, collection_id),
                    FOREIGN KEY(user_id) REFERENCES users (id),
                    FOREIGN KEY(collection_id) REFERENCES collections (id)
                )
            """))
            print("Created collection_favorites table.")
    except Exception as e:
        print(f"Error creating collection_favorites table: {e}")

    print("Migration complete.")

if __name__ == "__main__":
    migrate_likes_favorites()
