from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_TOP_KEYS_V25 = [
    "version",
    "top_level_folders",
    "naming_policy",
    "controlled_vocabularies",
    "folder_structure",
    "routing_rules",
    "archive_rules",
    "required_fields",
    "path_length_limits",
]

REQUIRED_TOP_KEYS_MASTER = [
    "schema",
    "enums",
    "type_catalog",
    "route_registry",
    "constraints",
    "topology",
    "workstreams",
]

DISALLOWED_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*]')
NON_ALNUM_DASH_UNDERSCORE = re.compile(r"[^A-Za-z0-9_-]+")
MULTI_DASH = re.compile(r"-+")


@dataclass(frozen=True)
class PolicyLoader:
    policy_path: Path
    policy: dict[str, Any]

    @classmethod
    def from_file(cls, policy_path: str | Path) -> "PolicyLoader":
        path = Path(policy_path)
        with path.open("r", encoding="utf-8") as f:
            policy = yaml.safe_load(f) or {}
        loader = cls(policy_path=path, policy=policy)
        loader.validate()
        return loader

    @property
    def _is_master(self) -> bool:
        """True when the loaded file is the master policy format (schema_version 3.x)."""
        return "schema" in self.policy

    def validate(self) -> None:
        if self._is_master:
            self._validate_master()
        else:
            self._validate_v25()

    def _validate_v25(self) -> None:
        missing = [k for k in REQUIRED_TOP_KEYS_V25 if k not in self.policy]
        if missing:
            raise ValueError(f"Missing required top-level policy keys: {missing}")

        version = str(self.policy.get("version", "")).strip().lower()
        if not version.startswith("v2_5"):
            raise ValueError(f"Unsupported policy version '{self.policy.get('version')}'. Expected v2_5 format.")

        vocab = self.policy["controlled_vocabularies"]
        if not vocab.get("status_tags"):
            raise ValueError("controlled_vocabularies.status_tags is required")
        if not vocab.get("phase_codes"):
            raise ValueError("controlled_vocabularies.phase_codes is required")

        routes = self.policy["routing_rules"].get("document_type_routing", {})
        if not routes:
            raise ValueError("routing_rules.document_type_routing must not be empty")

        type_cfg = self.policy["naming_policy"]["typeid"]["encoding"]["types"]
        if not type_cfg:
            raise ValueError("naming_policy.typeid.encoding.types must not be empty")

    def _validate_master(self) -> None:
        missing = [k for k in REQUIRED_TOP_KEYS_MASTER if k not in self.policy]
        if missing:
            raise ValueError(f"Missing required top-level master policy keys: {missing}")

        schema_version = str(self.policy["schema"].get("schema_version", "")).strip()
        if not schema_version.startswith("3."):
            raise ValueError(
                f"Unsupported master policy schema_version '{schema_version}'. Expected 3.x format."
            )

        enums = self.policy["enums"]
        if not enums.get("statuses"):
            raise ValueError("enums.statuses is required")
        if not enums.get("phases"):
            raise ValueError("enums.phases is required")
        if not self.policy.get("route_registry"):
            raise ValueError("route_registry must not be empty")
        if not self.policy.get("type_catalog"):
            raise ValueError("type_catalog must not be empty")

    @property
    def status_tags(self) -> list[str]:
        if self._is_master:
            return list(self.policy["enums"]["statuses"])
        return list(self.policy["controlled_vocabularies"]["status_tags"])

    @property
    def phase_codes(self) -> dict[str, str]:
        if self._is_master:
            return dict(self.policy["enums"]["phases"])
        return dict(self.policy["controlled_vocabularies"]["phase_codes"])

    @property
    def doc_types(self) -> dict[str, dict[str, Any]]:
        if self._is_master:
            registry = self.policy.get("route_registry", {})
            doc_map: dict[str, dict[str, Any]] = {}
            for doc_type, cfg in registry.items():
                cfg_dict = dict(cfg or {})
                # Provide stable 'default_folder' key for callers
                if "default_folder" not in cfg_dict:
                    cfg_dict["default_folder"] = (
                        cfg_dict.get("target_folder_id")
                        or cfg_dict.get("fallback_folder_id")
                    )
                doc_map[doc_type] = cfg_dict
            return doc_map
        # v2.5: document behavior under routing_rules.document_type_routing
        routing = self.policy.get("routing_rules", {}).get("document_type_routing", {})
        doc_map: dict[str, dict[str, Any]] = {}
        for doc_type, cfg in routing.items():
            cfg_dict = dict(cfg or {})
            if "default_folder" not in cfg_dict:
                cfg_dict["default_folder"] = cfg_dict.get("fixed_folder") or cfg_dict.get("fallback_folder")
            doc_map[doc_type] = cfg_dict
        return doc_map

    @property
    def phase_folder_map(self) -> dict[str, str]:
        # Both v2.5 and master use workstream-first filing; phase is not a folder axis.
        return {}

    @property
    def lifecycle_folders(self) -> list[str]:
        if self._is_master:
            return [
                ws["root_folder"]["name"]
                for ws in self.policy["workstreams"].values()
                if ws.get("root_folder")
            ]
        return list(self.policy["folder_structure"]["asset_standard_subfolders"].get("workstream_folders", []))

    @property
    def company_root_subfolders(self) -> list[str]:
        if self._is_master:
            return list(self.policy["topology"]["company_root"]["subfolders"].keys())
        if "company_root_subfolders" in self.policy:
            return list(self.policy["company_root_subfolders"])
        return list(self.policy["folder_structure"]["roots"]["COMPANY_ROOT"].get("subfolders", []))

    @property
    def special_folders(self) -> list[str]:
        if self._is_master:
            return list(self.policy["special_asset_folders"].keys())
        return list(self.policy["folder_structure"]["asset_standard_subfolders"].get("special_top_level_folders", []))

    @property
    def path_limits(self) -> dict[str, Any]:
        if self._is_master:
            c = self.policy.get("constraints", {})
            return {
                "max_full_path_chars": c.get("max_full_path_chars", 240),
                "max_filename_chars": c.get("max_filename_chars", 120),
                "max_foldername_chars": c.get("max_foldername_chars", 64),
                "max_description_chars": c.get("max_description_chars", 60),
                "max_project_name_chars": c.get("max_project_name_chars", 40),
                "max_location_chars": c.get("max_location_chars", 30),
                "max_depth_segments": c.get("max_depth_segments", 12),
            }
        constraints = dict(self.policy.get("naming_policy", {}).get("constraints", {}))
        pll = dict(self.policy.get("path_length_limits", {}))
        return {
            "max_full_path_chars": pll.get("hard_limit_full_path", constraints.get("max_full_path_chars", 240)),
            "max_filename_chars": pll.get("hard_limit_filename", constraints.get("max_filename_chars", 120)),
            "max_foldername_chars": constraints.get("max_foldername_chars", 64),
            "max_description_chars": pll.get("soft_limit_description", constraints.get("max_description_chars", 60)),
            "max_project_name_chars": pll.get("soft_limit_project_name", constraints.get("max_project_name_chars", 40)),
            "max_location_chars": pll.get("soft_limit_location", constraints.get("max_location_chars", 30)),
            "max_depth_segments": constraints.get("max_depth_segments", 12),
        }

    @property
    def type_configs(self) -> dict[str, dict[str, Any]]:
        if self._is_master:
            return dict(self.policy["type_catalog"])
        return dict(self.policy["naming_policy"]["typeid"]["encoding"]["types"])

    @property
    def workstreams(self) -> dict[str, dict[str, Any]]:
        """Return the workstream map (master format only; empty dict for v2.5)."""
        if self._is_master:
            return dict(self.policy["workstreams"])
        return {}

    def normalize_token(self, value: str, max_len: int | None = None) -> str:
        value = str(value).strip().replace(" ", "-")
        value = DISALLOWED_CHARS_PATTERN.sub("", value)
        value = NON_ALNUM_DASH_UNDERSCORE.sub("-", value)
        value = MULTI_DASH.sub("-", value).strip("-._ ")
        if max_len is not None:
            value = value[:max_len].rstrip("-._ ")
        return value or "UNSPECIFIED"

    def encode_typeid(self, type_code: str, value: float | int, unit: str) -> str:
        type_code = type_code.upper()
        type_cfg = self.type_configs.get(type_code)
        if not type_cfg:
            raise KeyError(f"Unknown type code: {type_code}")

        unit = str(unit).strip()
        allowed = set(type_cfg.get("allowed_input_units", []))
        if unit not in allowed:
            raise ValueError(f"Unit '{unit}' not allowed for {type_code}. Allowed: {sorted(allowed)}")

        metric_int = self._to_base_value(type_code=type_code, value=value, unit=unit)
        if metric_int < 0:
            raise ValueError("Metric must be non-negative")
        major = metric_int // 1000
        minor = metric_int % 1000
        metric = f"{major:02d}p{minor:03d}"
        return f"{type_code}{metric}"

    def _to_base_value(self, type_code: str, value: float | int, unit: str) -> int:
        value = float(value)
        if type_code in {"PVS", "WND", "DTC"}:
            if unit == "MW":
                return round(value * 1000)
            if unit in {"kW", "kWp"}:
                return round(value)
        if type_code == "BES":
            if unit == "MWh":
                return round(value * 1000)
            if unit == "kWh":
                return round(value)
        if type_code == "HYP":
            if unit == "ha":
                return round(value * 10000)
            if unit == "m2":
                return round(value)
        if type_code in {"HTL", "BCH", "BMS", "HYD", "OTH"}:
            return round(value)
        raise ValueError(f"Conversion rule not implemented for {type_code} / {unit}")

    def make_typeid(self, type_code: str, value: float | int, unit: str, sequence: int) -> str:
        if not 1 <= int(sequence) <= 99:
            raise ValueError("sequence must be between 1 and 99")
        return f"{self.encode_typeid(type_code, value, unit)}-{int(sequence):02d}"

    def make_company_folder(self, namecode: str, typ3: str, vat9: str) -> str:
        vat = str(vat9).zfill(9)
        token_name = self.normalize_token(namecode, max_len=self.path_limits.get("max_foldername_chars"))
        token_typ = self.normalize_token(str(typ3).upper(), max_len=3)
        return f"{token_name}-{token_typ}-{vat}"

    def make_asset_folder(self, typeid: str, project_name: str, location: str) -> str:
        project = self.normalize_token(project_name, max_len=self.path_limits.get("max_project_name_chars"))
        location_token = self.normalize_token(location, max_len=self.path_limits.get("max_location_chars"))
        return f"{typeid}_{project}_{location_token}"

    def make_internal_filename(
        self,
        *,
        typeid: str,
        phase: str,
        doc_type: str,
        description: str,
        date_yyyymmdd: str,
        version: str,
        status: str,
        ext: str,
    ) -> str:
        phase = phase.upper()
        doc_type = doc_type.upper()
        status = status.upper()
        version = version if str(version).startswith("v") else f"v{int(version):02d}"
        ext = str(ext).lower().lstrip(".")

        if phase not in self.phase_codes:
            raise ValueError(f"Unknown phase code: {phase}")
        if doc_type not in self.doc_types:
            raise ValueError(f"Unknown document type: {doc_type}")
        if status not in self.status_tags:
            raise ValueError(f"Unknown status tag: {status}")
        if not re.fullmatch(r"\d{8}", str(date_yyyymmdd)):
            raise ValueError("date must be in YYYYMMDD format")
        if not re.fullmatch(r"v\d{2}", str(version)):
            raise ValueError("version must be like v01")

        desc = self.normalize_token(description, max_len=self.path_limits.get("max_description_chars"))
        filename = f"{typeid}_{phase}_{doc_type}_{desc}_{date_yyyymmdd}_{version}_{status}.{ext}"
        max_filename_chars = self.path_limits.get("max_filename_chars")
        if max_filename_chars and len(filename) > max_filename_chars:
            overflow = len(filename) - max_filename_chars
            desc = self.normalize_token(desc, max_len=max(8, len(desc) - overflow))
            filename = f"{typeid}_{phase}_{doc_type}_{desc}_{date_yyyymmdd}_{version}_{status}.{ext}"
        return filename

    def build_asset_root(self, company_folder: str, asset_folder: str, archive_year: int | None = None) -> Path:
        if archive_year is None:
            return Path(company_folder) / "ASSETS" / asset_folder
        return Path(company_folder) / "ARCHIVE" / str(archive_year) / asset_folder

    def default_folder_for(self, phase: str, doc_type: str) -> str:
        phase = phase.upper()
        doc_type = doc_type.upper()
        doc_cfg = self.doc_types[doc_type]
        folder = doc_cfg.get("fixed_folder") or doc_cfg.get("fallback_folder") or doc_cfg.get("default_folder")
        if not folder:
            raise ValueError(f"No default routing folder configured for document type: {doc_type}")
        if "{PHASE_FOLDER}" in folder:
            folder = folder.replace("{PHASE_FOLDER}", self.phase_folder_map[phase])
        return folder

    def compile_filename_regex(self) -> re.Pattern[str]:
        phases = "|".join(sorted(map(re.escape, self.phase_codes.keys()), key=len, reverse=True))
        doctypes = "|".join(sorted(map(re.escape, self.doc_types.keys()), key=len, reverse=True))
        statuses = "|".join(sorted(map(re.escape, self.status_tags), key=len, reverse=True))
        types = "|".join(sorted(map(re.escape, self.type_configs.keys()), key=len, reverse=True))
        pattern = (
            rf"^(?P<typeid>(?:{types})\d{{2}}p\d{{3}}-\d{{2}})_"
            rf"(?P<phase>{phases})_"
            rf"(?P<doc_type>{doctypes})_"
            rf"(?P<description>[A-Za-z0-9_-]+)_"
            rf"(?P<date>\d{{8}})_"
            rf"(?P<version>v\d{{2}})_"
            rf"(?P<status>{statuses})"
            rf"\.(?P<ext>[A-Za-z0-9]+)$"
        )
        return re.compile(pattern)

    def parse_internal_filename(self, filename: str) -> dict[str, str] | None:
        match = self.compile_filename_regex().match(filename)
        if not match:
            return None
        return match.groupdict()
