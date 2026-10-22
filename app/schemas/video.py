from pydantic import BaseModel, Field


class VideoTokenRequest(BaseModel):
    appointment_id: str = Field(min_length=1, max_length=64)


class VideoTokenResponse(BaseModel):
    token: str
    channelName: str
