import os
from sqlmodel import Session, select, create_engine, text
from app.data_models import Video, Tag, VideoTag

# Setup Database connection
DATABASE_URL = "postgresql://myvideo:myvideo_password@localhost:5432/myvideo_db"
engine = create_engine(DATABASE_URL)

def migrate_tags():
    print("Starting Tag Migration...")

    with Session(engine) as session:
        # 1. Ensure new tables exist (Tag, VideoTag)
        # Assuming app startup already created them or we can force create here if needed.
        # But usually main.py startup event does it.
        # Let's assume user runs this AFTER restarting the app once, OR we can call create_all.
        from sqlmodel import SQLModel
        SQLModel.metadata.create_all(engine)
        print("Ensured tables exist.")

        # 2. Fetch all videos.
        # Since we removed the 'tags' field from the SQLModel 'Video' class,
        # session.exec(select(Video)) will return Video objects WITHOUT the tags data.
        # We need to fetch the raw JSON data using SQL.

        statement = text("SELECT id, tags FROM videos")
        results = session.connection().execute(statement).fetchall()

        migrated_count = 0
        total_videos = len(results)

        print(f"Found {total_videos} videos to check.")

        for row in results:
            video_id = row[0]
            tags_json = row[1] # This should be a list of strings or None

            if not tags_json:
                continue

            # tags_json comes from DB JSON column, SQLAlchemy handles deserialization usually.
            # If it's a string representation of JSON, we might need json.loads,
            # but usually with psycopg2/sqlalchemy it's already a python list.

            if isinstance(tags_json, str):
                import json
                try:
                    tags_list = json.loads(tags_json)
                except:
                    tags_list = []
            else:
                tags_list = tags_json

            if not tags_list:
                continue

            print(f"Migrating video {video_id}: {tags_list}")

            # Clean and Insert Tags
            # We use the NEW logic: allow .+#
            import re

            # Re-implement clean logic here or import
            # Let's just trust the old tags are "okayish" but maybe we want to normalize them to new Tag entities

            for tag_name in tags_list:
                if not tag_name: continue
                tag_name = tag_name.strip()

                # Check if tag exists
                tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
                if not tag:
                    tag = Tag(name=tag_name, usage_count=0)
                    session.add(tag)
                    session.commit()
                    session.refresh(tag)

                # Check if relation exists
                link = session.exec(select(VideoTag).where(VideoTag.video_id == video_id, VideoTag.tag_id == tag.id)).first()
                if not link:
                    link = VideoTag(video_id=video_id, tag_id=tag.id)
                    session.add(link)

                    # Update usage count
                    tag.usage_count += 1
                    session.add(tag)

            migrated_count += 1

        session.commit()
        print(f"Migration complete. Processed {migrated_count} videos with tags.")

if __name__ == "__main__":
    migrate_tags()
