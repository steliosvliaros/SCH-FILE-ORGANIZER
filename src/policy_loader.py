from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml


class PolicyError(Exception):
    """Raised when the YAML policy is invalid or incomplete."""


REQUIRED_TOP_KEYS = {
    "top_level_folders",
    "naming_policy",
    "controlled_vocabularies",
    "folder_structure",
    "document_types",
    "folder_buckets",
    "archive_rules",
    "required_fields",
    "path_length_limits",
    "special_storage_policy",
}

DISALLOWED_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*]')
NON_ALNUM_DASH_UNDERSCORE = re.compile(r"[^A-Za-z0-9_-]+")
REPEATED_DASHES = re.compile(r"-{2,}")
SPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class PolicyConfig:
    """Convenient wrapper around the YAML policy file."""

    source_path: Path
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "PolicyConfig":
        source_path = Path(path)
        if not source_path.exists():
            raise FileNotFoundError(f"Policy file not found: {source_path}")

        with source_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)

        if not isinstance(raw, dict):
            raise PolicyError("The policy root must be a mapping/dictionary.")

        config = cls(source_path=source_path, raw=raw)
        config.validate()
        return config

    def validate(self) -> None:
        missing = sorted(REQUIRED_TOP_KEYS.difference(self.raw.keys()))
        if missing:
            raise PolicyError(f"Missing required top-level keys: {missing}")

        if not isinstance(self.raw["top_level_folders"], list) or not self.raw["top_level_folders"]:
            raise PolicyError("top_level_folders must be a non-empty list")

        statuses = self.status_tags
        if not statuses:
            raise PolicyError("controlled_vocabularies.status_tags must be non-empty")

        phases = self.phase_folder_map
        if not phases:
            raise PolicyError("folder_buckets.PHASE_FOLDER_MAP must be non-empty")

        doc_types = self.document_types
        if not doc_types:
            raise PolicyError("document_types must be non-empty")

    @property
    def top_level_folders(self) -> list[str]:
        return list(self.raw["top_level_folders"])

    @property
    def naming_policy(self) -> Mapping[str, Any]:
        return self.raw["naming_policy"]

    @property
    def document_types(self) -> Mapping[str, Any]:
        return self.raw["document_types"]

    @property
    def status_tags(self) -> list[str]:
        return list(self.raw["controlled_vocabularies"]["status_tags"])

    @property
    def phase_codes(self) -> Mapping[str, str]:
        return self.raw["controlled_vocabularies"]["phase_codes"]

    @property
    def phase_folder_map(self) -> Mapping[str, str]:
        return self.raw["folder_buckets"]["PHASE_FOLDER_MAP"]

    @property
    def required_fields(self) -> list[str]:
        return list(self.raw["required_fields"])

    @property
    def path_limits(self) -> Mapping[str, Any]:
        return self.raw["path_length_limits"]["recommended_limits"]

    @property
    def type_encoding(self) -> Mapping[str, Any]:
        return self.raw["naming_policy"]["typeid"]["encoding"]["types"]

    def get_doc_type_default_folder(self, doc_type: str, phase: str | None = None) -> str:
        doc_type = doc_type.upper()
        definition = self.document_types.get(doc_type)
        if not definition:
            raise PolicyError(f"Unknown document type: {doc_type}")
        default_folder = str(definition["default_folder"])
        if "{PHASE_FOLDER}" in default_folder:
            if not phase:
                raise PolicyError("phase is required for doc types using {PHASE_FOLDER}")
            phase_folder = self.phase_folder_map.get(phase.upper())
            if not phase_folder:
                raise PolicyError(f"Unknown phase code: {phase}")
            default_folder = default_folder.replace("{PHASE_FOLDER}", phase_folder)
        return default_folder

    def sanitize_component(self, value: str, *, max_length: int | None = None) -> str:
        sanitized = sanitize_component(value)
        if max_length is not None:
            sanitized = sanitized[:max_length].rstrip("-._")
        return sanitized

    def render_company_folder(self, *, namecode: str, typ3: str, vat9: str) -> str:
        typ3 = typ3.upper()
        if len(vat9) != 9 or not vat9.isdigit():
            raise PolicyError("VAT9 must be exactly 9 digits")
        data = {
            "NAMECODE": self.sanitize_component(namecode, max_length=self.path_limits["max_foldername_chars"]),
            "TYP3": typ3,
            "VAT9": vat9,
        }
        template = self.naming_policy["company_folder"]["pattern"]
        return template.format(**data)

    def encode_typeid(self, *, asset_type: str, metric_value: float | int, sequence: int) -> str:
        asset_type = asset_type.upper()
        if asset_type not in self.type_encoding:
            raise PolicyError(f"Unknown asset type: {asset_type}")
        if sequence < 1 or sequence > 99:
            raise PolicyError("sequence must be between 1 and 99")

        metric_int = int(round(metric_value))
        metric_str = f"{metric_int:05d}"
        metric_token = f"{metric_str[:2]}p{metric_str[2:]}"
        return f"{asset_type}{metric_token}-{sequence:02d}"

    def render_asset_folder(self, *, typeid: str, project_name: str, location: str) -> str:
        max_folder_len = self.path_limits["max_foldername_chars"]
        project_name = self.sanitize_component(project_name, max_length=min(40, max_folder_len))
        location = self.sanitize_component(location, max_length=min(30, max_folder_len))
        return self.naming_policy["asset_folder"]["pattern"].format(
            TYPEID=typeid,
            PROJECT_NAME=project_name,
            LOCATION=location,
        )

    def render_internal_filename(
        self,
        *,
        typeid: str,
        phase: str,
        doc_type: str,
        description: str,
        date_yyyymmdd: str,
        version: str,
        status: str,
        extension: str,
    ) -> str:
        phase = phase.upper()
        doc_type = doc_type.upper()
        status = status.upper()
        extension = extension.lower().lstrip(".")
        if phase not in self.phase_folder_map:
            raise PolicyError(f"Unknown phase: {phase}")
        if doc_type not in self.document_types:
            raise PolicyError(f"Unknown document type: {doc_type}")
        if status not in self.status_tags:
            raise PolicyError(f"Unknown status tag: {status}")
        if not re.fullmatch(r"\d{8}", date_yyyymmdd):
            raise PolicyError("date_yyyymmdd must be in YYYYMMDD format")
        if not re.fullmatch(r"v\d{2}", version):
            raise PolicyError("version must be like v01, v02")

        description = self.sanitize_component(
            description,
            max_length=self.path_limits["max_description_chars"],
        )

        template = self.document_types[doc_type].get(
            "filename_template",
            self.naming_policy["filenames"]["internal"]["template"],
        )
        return template.format(
            TYPEID=typeid,
            PHASE=phase,
            DOCTYPE=doc_type,
            DESCRIPTION=description,
            DATE=date_yyyymmdd,
            VERSION=version,
            STATUS=status,
            EXT=extension,
        )

    def portable_export_filename(self, *, company_folder: str, internal_filename: str) -> str:
        template = self.naming_policy["filenames"]["portable_export"]["template"]
        return template.format(COMPANY_FOLDER=company_folder, INTERNAL_FILENAME=internal_filename)

    def build_active_asset_root(self, *, company_folder: str, asset_folder: str) -> str:
        template = self.raw["folder_structure"]["roots"]["ASSETS"]["path_template"]
        return template.format(COMPANY_FOLDER=company_folder, ASSET_FOLDER=asset_folder)

    def build_archive_asset_root(self, *, year: int | str, company_folder: str, asset_folder: str) -> str:
        template = self.raw["folder_structure"]["roots"]["ARCHIVE"]["path_template"]
        return template.format(YEAR=year, COMPANY_FOLDER=company_folder, ASSET_FOLDER=asset_folder)

    def filename_pattern(self) -> re.Pattern[str]:
        phase_part = "|".join(sorted(map(re.escape, self.phase_folder_map.keys())))
        doc_part = "|".join(sorted(map(re.escape, self.document_types.keys()), key=len, reverse=True))
        status_part = "|".join(sorted(map(re.escape, self.status_tags), key=len, reverse=True))
        pattern = (
            r"^(?P<typeid>[A-Z]{3}\d{2}p\d{3}-\d{2})_"
            rf"(?P<phase>{phase_part})_"
            rf"(?P<doc_type>{doc_part})_"
            r"(?P<description>[A-Za-z0-9_-]+)_"
            r"(?P<date>\d{8})_"
            r"(?P<version>v\d{2})_"
            rf"(?P<status>{status_part})\."
            r"(?P<ext>[A-Za-z0-9]+)$"
        )
        return re.compile(pattern)


def sanitize_component(value: str) -> str:
    value = value.strip()
    value = SPACE_PATTERN.sub("-", value)
    value = DISALLOWED_CHARS_PATTERN.sub("-", value)
    value = NON_ALNUM_DASH_UNDERSCORE.sub("-", value)
    value = REPEATED_DASHES.sub("-", value)
    value = value.strip("-._ ")
    return value or "UNSPECIFIED"


def require_keys(mapping: Mapping[str, Any], keys: Iterable[str], *, context: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise PolicyError(f"Missing keys in {context}: {missing}")
