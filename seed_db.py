"""Локальный seed тестовых данных для разработки.

Приведено к текущему API фасада database_manager: `link_family` больше не
существует (разделён на link_parent_to_family / link_student_to_family), а
`add_parent` принимает role='admin' вместо is_admin=True.
"""
import logging

from src.database_manager import (
    add_family,
    add_parent,
    add_student,
    link_parent_to_family,
    link_student_to_family,
)

logger = logging.getLogger(__name__)


def seed():
    logger.info("Seeding test data...")
    # Add a test family
    fam_id = add_family("Test Family")

    # Add a test parent (change phone if testing actual bot)
    parent_id = add_parent("Test Parent", "79991234567", role='admin')

    # Add a test student with a known public sheet (or user provided one)
    student_id = add_student("Test Student", "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms")

    # Link parent and student to the family
    link_parent_to_family(fam_id, parent_id)
    link_student_to_family(fam_id, student_id)
    logger.info("Seeding complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed()
