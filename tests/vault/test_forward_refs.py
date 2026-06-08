# SPDX-License-Identifier: Apache-2.0
from cairn.vault import parse_note


def test_links_to_nonexistent_target_parses_cleanly():
    note = parse_note("---\ntitle: A\n---\nSee [[Does Not Exist Yet]].\n\n- depends_on [[Also Missing]]\n")
    assert "Does Not Exist Yet" in note.wikilinks
    assert any(r.target == "Also Missing" and r.rel_type == "depends_on" for r in note.relations)
