from pydantic import BaseModel
from typing import Optional


class ChatQueryReq(BaseModel):
    question: str
    session_id: str = ""


class ChatQueryResp(BaseModel):
    answer: str
    source_list: list[dict] = []
    risk_tips: str = ""
    doc_count: int = 0
    segment_count: int = 0


class ChatFeedbackReq(BaseModel):
    session_id: str
    query: str
    response: str
    feedback_type: int = 0
    bad_reason: str = ""


class DocUploadReq(BaseModel):
    category: str
    effective_time: str = ""


class DocTreeResp(BaseModel):
    doc_uuid: str
    title: str
    chapter_list: list[dict] = []


class SpiderSiteReq(BaseModel):
    site_name: str
    site_url: str
    yaml_config: str = ""


class SpiderAuditReq(BaseModel):
    site_id: int
    approve: bool


class CommonResp(BaseModel):
    code: int = 200
    msg: str = "操作成功"
    data: Optional[dict | list | None] = None


class EvalRunReq(BaseModel):
    version: str = "v1.0"
    sample_count: int = 50
    eval_set_id: str = ""
