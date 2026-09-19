"""FastAPI application exposing the GS1 element-string / Digital Link parser."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .gs1 import GS1ParseError, parse

app = FastAPI(
    title="GS1 Element String Parser",
    version="1.1.0",
    description=(
        "Parses raw GS1 scan strings (FNC1 / ASCII 29 separated) or GS1 "
        "Digital Link URIs (http:// / https://) into structured, ordered AI "
        "elements. Supports AI 01 (GTIN), 10 (batch/lot), 17 (expiry date) "
        "and 21 (serial number)."
    ),
)


class ParseRequest(BaseModel):
    data: str = Field(
        ...,
        title="Raw scan string or GS1 Digital Link URI",
        description=(
            "Raw GS1 element string as scanned (variable-length fields are "
            "separated by ASCII 29, encoded in JSON as \\u001d) or a GS1 "
            "Digital Link URI carrying AI/value pairs in the path and AI "
            "query parameters."
        ),
        examples=[
            "01095011015300031725013110ABC123\u001d21SN0001",
            "https://id.example.com/01/09501101530003/10/ABC123?17=250131&21=SN0001",
        ],
    )


class Element(BaseModel):
    ai: str = Field(description="Application identifier, e.g. '01'")
    name: str = Field(description="Short element name, e.g. 'GTIN'")
    value: str = Field(description="Field value without AI and separators")
    offset: int = Field(description="Byte offset of the AI within the input")


class ParseResponse(BaseModel):
    elements: list[Element]


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/parse", response_model=ParseResponse)
def parse_scan(req: ParseRequest) -> ParseResponse:
    try:
        elements = parse(req.data)
    except GS1ParseError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    return ParseResponse(elements=[Element(**vars(e)) for e in elements])
