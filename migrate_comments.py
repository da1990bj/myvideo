import os
from sqlalchemy import create_engine, text

# Setup Database connection
DATABASE_URL = "postgresql://myvideo:myvideo_password@localhost:5432/myvideo_db"
engine = create_engine(DATABASE_URL)

def migrate_comments():
    print("Starting Comment Migration...")

    # 1. Add parent_id column to comments
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE comments ADD COLUMN parent_id INTEGER REFERENCES comments(id)"))
            print("Added parent_id column.")
    except Exception as e:
        print(f"Skipping parent_id (maybe exists): {e}")

    # 2. Add like_count column to comments
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE comments ADD COLUMN like_count INTEGER DEFAULT 0"))
            print("Added like_count column.")
    except Exception as e:
        print(f"Skipping like_count (maybe exists): {e}")

    # 3. Create comment_likes table
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS comment_likes (
                    user_id UUID NOT NULL,
                    comment_id INTEGER NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    PRIMARY KEY (user_id, comment_id),
                    FOREIGN KEY(user_id) REFERENCES users (id),
                    FOREIGN KEY(comment_id) REFERENCES comments (id)
                )
            """))
            print("Created comment_likes table.")
    except Exception as e:
        print(f"Error creating comment_likes: {e}")

    # 4. Add is_deleted and deleted_by to comments
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE comments ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE"))
            print("Added is_deleted column.")
    except Exception as e:
        print(f"Skipping is_deleted (maybe exists): {e}")

    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE comments ADD COLUMN deleted_by VARCHAR"))
            print("Added deleted_by column.")
    except Exception as e:
        print(f"Skipping deleted_by (maybe exists): {e}")

    print("Migration complete.")

if __name__ == "__main__":
    migrate_comments()
