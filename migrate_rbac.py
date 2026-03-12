import os
from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy import text
from app.data_models import User, Role, SystemConfig, AdminLog

# Setup Database connection
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://myvideo:myvideo_password@localhost:5432/myvideo_db")
engine = create_engine(DATABASE_URL)

def migrate_rbac():
    print("Starting RBAC Migration...")

    # 1. Create new tables (Role, SystemConfig, AdminLog)
    # This will create tables that don't exist.
    # Note: It won't update 'users' table because it already exists.
    print("Creating new tables...")
    SQLModel.metadata.create_all(engine)

    # 2. Add role_id to users table manually
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN role_id INTEGER REFERENCES roles(id)"))
            print("Added role_id column to users table.")
    except Exception as e:
        # If column already exists, this might fail, which is fine for idempotency (mostly)
        # Better checking would be querying information_schema, but this is simple migration
        print(f"Skipping adding role_id to users (maybe exists): {e}")

    # 3. Initialize Data
    with Session(engine) as session:
        # Check if roles exist to avoid duplicates
        if not session.exec(select(Role)).first():
            print("Initializing default roles...")

            # Define Roles
            roles = [
                Role(name="Super Admin", description="Has all permissions", permissions="*"),
                Role(name="Content Auditor", description="Can audit videos and comments", permissions="video:audit,video:ban,comment:delete"),
                Role(name="Operations", description="Content operations", permissions="video:recommend,collection:manage"),
                Role(name="User Support", description="Manage users", permissions="user:ban,user:reset"),
                Role(name="Standard User", description="Default user role", permissions="video:upload,comment:create,social:interaction,user:be_followed"),
                Role(name="Muted User", description="Cannot comment", permissions="video:upload,social:interaction"),
                Role(name="Restricted User", description="Cannot upload", permissions="comment:create,social:interaction")
            ]

            for role in roles:
                session.add(role)
            session.commit()
            print("Roles initialized.")

            # Refresh roles to get IDs
            roles_map = {r.name: r for r in session.exec(select(Role)).all()}

            # 4. Migrate Users
            print("Migrating users to roles...")

            # Admins -> Super Admin
            admins = session.exec(select(User).where(User.is_admin == True)).all()
            for admin in admins:
                admin.role_id = roles_map["Super Admin"].id
                session.add(admin)

            # Non-Admins -> Standard User
            # Check users with no role
            users = session.exec(select(User).where(User.role_id == None)).all()
            for user in users:
                user.role_id = roles_map["Standard User"].id
                session.add(user)

            session.commit()
            print(f"Migrated {len(admins)} admins and {len(users)} users.")
        else:
            print("Roles already exist. Skipping role initialization.")

        # 5. Initialize System Config
        if not session.exec(select(SystemConfig)).first():
            print("Initializing system config...")
            configs = [
                SystemConfig(key="site_name", value="MyVideo Site", description="The name of the website"),
                SystemConfig(key="maintenance_mode", value="false", description="Enable maintenance mode"),
                SystemConfig(key="allow_registration", value="true", description="Allow new user registration"),
                SystemConfig(key="max_upload_size_mb", value="500", description="Maximum video upload size in MB"),
                SystemConfig(key="site_notice", value="", description="Global site notice message")
            ]
            for config in configs:
                session.add(config)
            session.commit()
            print("System config initialized.")
        else:
             print("System config already exists. Skipping initialization.")

    print("Migration complete.")

if __name__ == "__main__":
    migrate_rbac()
