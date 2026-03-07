from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

import pandas as pd

from .inventory import InventoryRecord
from .policy_loader import PolicyConfig


@dataclass(slots=True)
class RuleDecision:
    absolute_path: str
    relative_path: str
    current_name: str
    policy_status: str
    action: str
    reason: str
    matched_phase: str | None
    matched_doc_type: str | None
    matched_status: str | None
    proposed_lifecycle_folder: str | None
    proposed_special_folder: str | None
    confidence: float
    content_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuleEngine:
    """Deterministic first-pass rules driven by the YAML policy."""

    def __init__(self, policy: PolicyConfig) -> None:
        self.policy = policy
        self.filename_regex = policy.filename_pattern()
        self.junk_extensions = self._load_junk_extensions()
        self.junk_filenames = self._load_junk_filenames()
        self.special_folder_names = set(policy.raw["special_storage_policy"]["special_folders"].keys())

    def classify_record(self, record: InventoryRecord, *, duplicate_hashes: set[str] | None = None) -> RuleDecision:
        duplicate_hashes = duplicate_hashes or set()
        ext = record.extension.lower()
        filename = record.name
        parent_parts = PurePosixPath(record.parent_path).parts if record.parent_path else ()

        if filename in self.junk_filenames or ext in self.junk_extensions:
            return RuleDecision(
                absolute_path=record.absolute_path,
                relative_path=record.relative_path,
                current_name=filename,
                policy_status="junk",
                action="archive_or_delete",
                reason="Matched archive_rules junk filename/extension rule.",
                matched_phase=None,
                matched_doc_type=None,
                matched_status=None,
                proposed_lifecycle_folder=None,
                proposed_special_folder=None,
                confidence=1.0,
                content_hash=record.content_hash,
            )

        if record.content_hash and record.content_hash in duplicate_hashes:
            return RuleDecision(
                absolute_path=record.absolute_path,
                relative_path=record.relative_path,
                current_name=filename,
                policy_status="duplicate",
                action="move_to_special_folder",
                reason="Exact duplicate hash found.",
                matched_phase=None,
                matched_doc_type=None,
                matched_status=None,
                proposed_lifecycle_folder=None,
                proposed_special_folder="_DUPLICATED",
                confidence=1.0,
                content_hash=record.content_hash,
            )

        filename_match = self.filename_regex.match(filename)
        if filename_match:
            groups = filename_match.groupdict()
            phase = groups["phase"]
            doc_type = groups["doc_type"]
            status = groups["status"]
            lifecycle_folder = self.policy.get_doc_type_default_folder(doc_type, phase=phase)

            if status == "SUPERSEDED":
                return RuleDecision(
                    absolute_path=record.absolute_path,
                    relative_path=record.relative_path,
                    current_name=filename,
                    policy_status="superseded",
                    action="move_to_special_folder",
                    reason="Filename already marked as SUPERSEDED.",
                    matched_phase=phase,
                    matched_doc_type=doc_type,
                    matched_status=status,
                    proposed_lifecycle_folder=lifecycle_folder,
                    proposed_special_folder="_SUPERSEDED",
                    confidence=1.0,
                    content_hash=record.content_hash,
                )

            in_special_folder = any(part in self.special_folder_names for part in parent_parts)
            if in_special_folder:
                status_label = "special_storage"
                action = "keep_in_place"
                reason = "File already lives under a policy-defined special folder."
            else:
                status_label = "policy_named"
                action = "review_path"
                reason = "Filename matches the policy template; validate folder placement next."

            return RuleDecision(
                absolute_path=record.absolute_path,
                relative_path=record.relative_path,
                current_name=filename,
                policy_status=status_label,
                action=action,
                reason=reason,
                matched_phase=phase,
                matched_doc_type=doc_type,
                matched_status=status,
                proposed_lifecycle_folder=lifecycle_folder,
                proposed_special_folder=None,
                confidence=0.95,
                content_hash=record.content_hash,
            )

        inferred_doc_type = self._infer_doc_type_from_parent(parent_parts)
        inferred_phase = self._infer_phase_from_parent(parent_parts)
        lifecycle_folder = None
        if inferred_doc_type:
            lifecycle_folder = self.policy.get_doc_type_default_folder(inferred_doc_type, phase=inferred_phase or "FS")
        elif inferred_phase:
            lifecycle_folder = self.policy.phase_folder_map.get(inferred_phase)

        return RuleDecision(
            absolute_path=record.absolute_path,
            relative_path=record.relative_path,
            current_name=filename,
            policy_status="review",
            action="manual_review",
            reason="Filename does not fully match the policy template; infer fields from folder context or content later.",
            matched_phase=inferred_phase,
            matched_doc_type=inferred_doc_type,
            matched_status=None,
            proposed_lifecycle_folder=lifecycle_folder,
            proposed_special_folder=None,
            confidence=0.45 if inferred_doc_type or inferred_phase else 0.2,
            content_hash=record.content_hash,
        )

    def classify_dataframe(self, inventory_df: pd.DataFrame) -> pd.DataFrame:
        duplicate_hashes = self.find_duplicate_hashes(inventory_df)
        decisions: list[dict[str, Any]] = []
        for row in inventory_df.to_dict(orient="records"):
            record = InventoryRecord(**row)
            decisions.append(self.classify_record(record, duplicate_hashes=duplicate_hashes).to_dict())
        return pd.DataFrame(decisions)

    @staticmethod
    def find_duplicate_hashes(inventory_df: pd.DataFrame) -> set[str]:
        if inventory_df.empty or "content_hash" not in inventory_df.columns:
            return set()
        hashed = inventory_df.dropna(subset=["content_hash"])
        duplicate_mask = hashed.duplicated(subset=["content_hash"], keep=False)
        return set(hashed.loc[duplicate_mask, "content_hash"].tolist())

    def _load_junk_extensions(self) -> set[str]:
        junk_extensions: set[str] = set()
        for rule in self.policy.raw["archive_rules"]:
            if "if_extension_in" in rule:
                junk_extensions.update(ext.lower() for ext in rule["if_extension_in"])
        return junk_extensions

    def _load_junk_filenames(self) -> set[str]:
        junk_filenames: set[str] = set()
        for rule in self.policy.raw["archive_rules"]:
            if "if_filename_in" in rule:
                junk_filenames.update(rule["if_filename_in"])
        return junk_filenames

    def _infer_doc_type_from_parent(self, parent_parts: tuple[str, ...]) -> str | None:
        for doc_type, definition in self.policy.document_types.items():
            default_folder = str(definition["default_folder"]).split("/")[0]
            if default_folder in parent_parts:
                return doc_type
        return None

    def _infer_phase_from_parent(self, parent_parts: tuple[str, ...]) -> str | None:
        for phase, folder in self.policy.phase_folder_map.items():
            if folder in parent_parts:
                return phase
        return None
