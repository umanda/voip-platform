from pydantic import BaseModel, Field


class BillingTickRequest(BaseModel):
    """
    Request from Lua to deduct credit for an elapsed billing period.

    Sent every 60 seconds during an active call.
    Legacy mapping: POST /api/v1/check/call/time (but per-tick, not block-based).

    call_uuid: FreeSWITCH UUID (Redis session key prefix).
    account_id: credits_customers.id (set in Redis session at authorize time).
    elapsed_seconds: seconds since last tick — typically 60.
    """

    call_uuid: str
    account_id: int
    elapsed_seconds: int = Field(gt=0, description="Seconds elapsed since last tick")


class BillingTickData(BaseModel):
    """
    Billing tick response. Lua reads 'continue' first.

    If continue is False, Lua MUST hangup the call immediately (R-FLOW-02).
    The 'continue' key name is preserved to match the architecture contract,
    even though it is a Python keyword — serialized correctly via model_dump().
    """

    continue_call: bool = Field(serialization_alias="continue")
    remaining_seconds: int
    deducted_amount: float

    model_config = {"populate_by_name": True}
