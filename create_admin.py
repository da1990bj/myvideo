from sqlmodel import Session, select
from app.database import engine
from app.data_models import User
from app.security import get_password_hash

def create_admin():
    with Session(engine) as session:
        # Check if admin exists
        user = session.exec(select(User).where(User.username == "admin")).first()
        if user:
            print("User 'admin' already exists. Promoting to admin...")
            user.is_admin = True
            user.hashed_password = get_password_hash("123456") # Ensure password is correct
            session.add(user)
        else:
            print("Creating new admin user...")
            user = User(
                username="admin",
                email="admin@example.com",
                hashed_password=get_password_hash("123456"),
                is_admin=True
            )
            session.add(user)

        session.commit()
        print("Admin user ready: admin / 123456")

if __name__ == "__main__":
    create_admin()
