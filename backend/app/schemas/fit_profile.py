from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
RequiredText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
Measurement = Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)]


class BrandSize(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand: RequiredText
    line: Text = ""
    size: RequiredText
    note: Text = ""


class SkipEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand: Text = ""
    line: Text = ""
    reason: RequiredText

    @model_validator(mode="after")
    def require_brand_or_line(self):
        if not self.brand and not self.line:
            raise ValueError("Brand or line required")
        return self


class FitProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rev: str = Field(min_length=1, max_length=64)
    brand_sizes: list[BrandSize] = Field(default_factory=list, max_length=100)
    skip_list: list[SkipEntry] = Field(default_factory=list, max_length=100)
    neck: Measurement | None = None
    sleeve: Measurement | None = None
    thigh: Measurement | None = None
    reference_garment: str = Field(default="", max_length=1000)
