from app.routes.schemas.base import BaseSchema


class UsageWindowOutput(BaseSchema):
    used: float
    limit: float


class UsageStatusOutput(BaseSchema):
    five_hour: UsageWindowOutput
    seven_day: UsageWindowOutput
