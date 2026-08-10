from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from transpodcast.database import Database
from transpodcast.models import ProcessingRunActivityRecord, ProcessingRunRecord


class ProcessingRunActivityRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record_translation_phase(
        self,
        processing_run_id: int,
        current_step: str,
        recorded_at: datetime,
    ) -> None:
        with self.database.session() as session:
            run = session.get(ProcessingRunRecord, processing_run_id)
            if run is None:
                return
            previous = session.scalar(
                select(ProcessingRunActivityRecord)
                .where(
                    ProcessingRunActivityRecord.processing_run_id
                    == processing_run_id,
                    ProcessingRunActivityRecord.kind == "translation_phase",
                )
                .order_by(ProcessingRunActivityRecord.id.desc())
                .limit(1)
            )
            if previous is not None and previous.stage == current_step:
                return
            session.add(
                ProcessingRunActivityRecord(
                    processing_run_id=processing_run_id,
                    kind="translation_phase",
                    stage=current_step,
                    summary=(
                        f"{current_step.replace('_', ' ').title()} started"
                    ),
                    metadata_json=json.dumps(
                        {"source": "translation-project"},
                        separators=(",", ":"),
                    ),
                    attempt_id=None,
                    created_at=recorded_at,
                )
            )
