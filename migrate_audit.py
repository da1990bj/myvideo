from sqlalchemy import text
from app.database import engine

def migrate():
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE videos ADD COLUMN ban_reason TEXT"))
            conn.commit()
            print("Added ban_reason to videos.")
        except Exception as e:
            print(f"ban_reason column might already exist: {e}")

if __name__ == "__main__":
    migrate()
