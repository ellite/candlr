import calendar
from pydantic import BaseModel, EmailStr, Field, model_validator
from typing import Annotated, Optional, Literal, Union
from datetime import datetime


# Auth
class UserRegister(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    username: str
    password: str


class TwoFactorRequired(BaseModel):
    requires_2fa: Literal[True] = True


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool
    has_password: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class AccountDelete(BaseModel):
    # Only required for accounts that have a local password; an OIDC-only
    # account has nothing to check it against.
    password: Optional[str] = None


class RegistrationStatusOut(BaseModel):
    enabled: bool


class PasswordResetStatusOut(BaseModel):
    enabled: bool


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


# OIDC
class OidcConfigOut(BaseModel):
    enabled: bool
    provider_name: str
    disable_password_login: bool


class OidcAuthorizeOut(BaseModel):
    auth_url: str
    state: str


class OidcExchangeRequest(BaseModel):
    code: str


# Admin
class UserAdminUpdate(BaseModel):
    is_admin: Optional[bool] = None


# Notifications
class NotificationChannelOut(BaseModel):
    channel: str
    enabled: bool
    config: dict


NOTIFY_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"  # 24h "HH:MM"


class NotifyPreferencesOut(BaseModel):
    notify_time: str


class NotifyPreferencesUpdate(BaseModel):
    notify_time: str = Field(pattern=NOTIFY_TIME_PATTERN)


class NotificationChannelUpdate(BaseModel):
    enabled: bool
    config: dict = {}


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionCreate(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys


class PushSubscriptionDelete(BaseModel):
    endpoint: str


class VapidPublicKeyOut(BaseModel):
    public_key: Optional[str]


# Events
class EventTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class EventTypeOut(BaseModel):
    id: int
    name: str
    is_default: bool

    model_config = {"from_attributes": True}


class EventTypeReorder(BaseModel):
    ids: list[int]


class EventInput(BaseModel):
    event_type_id: int
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    year: Optional[int] = Field(default=None, ge=1, le=2100)
    year_known: bool = True
    notes: Optional[str] = Field(default=None, max_length=10000)
    notify: bool = True

    @model_validator(mode="after")
    def _validate_date(self):
        if not self.year_known:
            self.year = None
        # A reference (leap) year when the year is unknown, so Feb 29 is
        # still accepted rather than rejected for lack of a concrete year.
        reference_year = self.year if self.year else 2000
        max_day = calendar.monthrange(reference_year, self.month)[1]
        if self.day > max_day:
            raise ValueError(f"{self.month}/{self.day} is not a valid date")
        return self


class EventOut(BaseModel):
    id: int
    event_type: EventTypeOut
    month: int
    day: int
    year: Optional[int]
    year_known: bool
    notes: Optional[str]
    notify: bool
    days_until: int
    days_since: int

    model_config = {"from_attributes": True}


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    event: EventInput


class ExistingEventInput(EventInput):
    id: int


class PersonUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    events: Optional[list[ExistingEventInput]] = None


class PersonOut(BaseModel):
    id: int
    name: str
    image_url: Optional[str]
    events: list[EventOut]

    model_config = {"from_attributes": True}


class ImageUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


# Bulk actions on cards
BulkIds = Annotated[list[int], Field(min_length=1, max_length=5000)]


class BulkDelete(BaseModel):
    action: Literal["delete"]
    ids: BulkIds


class BulkSetType(BaseModel):
    """Changes dates of one type to another on the chosen cards. It swaps a
    specific type rather than assigning one, so a card holding both a
    Birthday and an Anniversary keeps them apart."""

    action: Literal["set_type"]
    ids: BulkIds
    from_event_type_id: int
    to_event_type_id: int


class BulkSetNotify(BaseModel):
    action: Literal["set_notify"]
    ids: BulkIds
    notify: bool


BulkRequest = Annotated[Union[BulkDelete, BulkSetType, BulkSetNotify], Field(discriminator="action")]


class BulkResult(BaseModel):
    people: int
    dates: int
