from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..auth import hash_password
from ..database import get_db
from ..images import delete_image
from ..models import User
from ..schemas import UserOut, UserAdminUpdate, UserRegister
from ..dependencies import get_admin_user
from ..seed import seed_default_event_types

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/users", response_model=list[UserOut])
def list_users(
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    return db.query(User).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    data: UserRegister,
    _: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin-created accounts bypass the enable_registrations / max-users
    settings entirely - those only gate self-service signup."""
    username_taken = db.query(User).filter(User.username == data.username).first()
    email_taken = db.query(User).filter(User.email == data.email).first()
    if username_taken or email_taken:
        raise HTTPException(status_code=400, detail="Username or email already registered")

    user = User(username=data.username, email=data.email, hashed_password=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    seed_default_event_types(db, user.id)
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    data: UserAdminUpdate,
    current_admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if data.is_admin is not None:
        if user.id == current_admin.id and not data.is_admin:
            raise HTTPException(status_code=400, detail="Cannot remove your own admin status")
        user.is_admin = data.is_admin
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Cascading the DB rows (via the relationships in models.py) doesn't
    # touch image files on disk, so those have to be cleaned up explicitly.
    for person in user.people:
        if person.image_filename:
            delete_image(person.image_filename)

    db.delete(user)
    db.commit()
    return {"ok": True}
