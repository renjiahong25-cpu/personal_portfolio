"""Tests for the eight accompaniment textures and their combination."""

from __future__ import annotations

import pytest

from services.arrangement.patterns import (
    BASS_HIGH,
    BASS_LOW,
    Role,
    place_bass,
)
from services.arrangement.textures import (
    COMBINABLE,
    TEXTURE_LABELS,
    TextureName,
    build_texture,
    build_texture_combined,
    build_texture_plan,
)


def test_every_registered_texture_builds() -> None:
    for name in TextureName:
        notes = build_texture(name, 9, "major")
        assert notes, f"{name} produced nothing"


def test_every_texture_label_is_unique() -> None:
    labels = list(TEXTURE_LABELS.values())
    assert len(labels) == len(set(labels))


def test_place_bass_lands_inside_the_bass_register() -> None:
    for root in range(12):
        pitch = place_bass(root)
        assert BASS_LOW <= pitch <= BASS_HIGH


def test_no_texture_note_falls_before_the_bar() -> None:
    for name in TextureName:
        for note in build_texture(name, 7, "minor"):
            assert note.beat >= 0.0, f"{name}"


def test_no_texture_note_overruns_the_bar() -> None:
    for name in TextureName:
        for note in build_texture(name, 3, "augmented"):
            assert note.beat + note.duration_beats <= 4.0 + 1e-9, f"{name}"


def test_no_texture_doubles_a_key_at_one_instant() -> None:
    for name in TextureName:
        seen: set[tuple[float, int]] = set()
        for note in build_texture(name, 2, "dominant7"):
            key = (note.beat, note.midi)
            assert key not in seen, f"{name} doubled {key}"
            seen.add(key)


def test_texture_notes_are_sorted_by_position() -> None:
    for name in TextureName:
        notes = build_texture(name, 5, "minor")
        assert notes == sorted(notes, key=lambda n: (n.beat, n.midi))


def test_block_chord_contains_the_full_triad() -> None:
    notes = build_texture(TextureName.BLOCK, 0, "major")
    pitches = {n.midi % 12 for n in notes}
    assert pitches == {0, 4, 7}
    assert place_bass(0) in {n.midi for n in notes}


def test_perc332_has_three_groups() -> None:
    notes = build_texture(TextureName.PERC332, 0, "major")
    assert len(notes) >= 6


def test_rhythm_has_syncopation() -> None:
    notes = build_texture(TextureName.RHYTHM, 0, "major")
    assert any(n.beat % 1.0 != 0.0 for n in notes)


def test_half_plays_bass_on_every_beat() -> None:
    notes = build_texture(TextureName.HALF, 0, "major")
    bass_notes = [n for n in notes if n.role == Role.SKELETON]
    assert len(bass_notes) == 4


def test_bass_is_the_simplest_texture() -> None:
    notes = build_texture(TextureName.BASS, 0, "major")
    assert len(notes) == 4
    assert all(n.role == Role.SKELETON for n in notes)


def test_arplong_spans_the_whole_bar() -> None:
    notes = build_texture(TextureName.ARLONG, 0, "major")
    assert len(notes) >= 4


def test_arpfull_has_one_note_per_subdivision() -> None:
    notes = build_texture(TextureName.ARPFULL, 0, "major")
    assert len(notes) >= 8


def test_riff_has_a_repeating_motif() -> None:
    notes = build_texture(TextureName.RIFF, 0, "major")
    assert len(notes) >= 8


def test_an_unknown_texture_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_texture("does-not-exist", 0, "major")


def test_combining_block_and_bass_works() -> None:
    notes = build_texture_combined([TextureName.BLOCK, TextureName.BASS], 0, "major")
    assert notes


def test_combining_incompatible_textures_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_texture_combined([TextureName.BLOCK, TextureName.PERC332], 0, "major")


def test_combined_texture_removes_duplicates() -> None:
    notes = build_texture_combined([TextureName.BLOCK, TextureName.BASS], 0, "major")
    seen: set[tuple[float, int]] = set()
    for note in notes:
        key = (note.beat, note.midi)
        assert key not in seen
        seen.add(key)


def test_texture_plan_maps_bars_to_textures() -> None:
    plan = build_texture_plan([(0, 4, TextureName.BLOCK), (4, 8, TextureName.HALF)])
    assert plan.texture_for_bar(0) == TextureName.BLOCK
    assert plan.texture_for_bar(3) == TextureName.BLOCK
    assert plan.texture_for_bar(4) == TextureName.HALF
    assert plan.texture_for_bar(7) == TextureName.HALF


def test_texture_plan_falls_back_to_default() -> None:
    plan = build_texture_plan([(0, 4, TextureName.BLOCK)], default_texture=TextureName.BASS)
    assert plan.texture_for_bar(10) == TextureName.BASS


def test_texture_plan_serialises() -> None:
    plan = build_texture_plan([(0, 4, "block"), (4, 8, "half")])
    payload = plan.to_dict()
    assert payload["defaultTexture"] == "half"
    assert len(payload["sections"]) == 2


def test_all_textures_are_combinable_with_bass() -> None:
    for name in TextureName:
        assert TextureName.BASS in COMBINABLE[name]


def test_texture_plan_parsing_in_service() -> None:
    """Test that texture_plan JSON string is parsed correctly in validate_request."""
    from services.arrangement.textures import TextureName, TexturePlan
    from services.score.service import validate_request

    # Valid texture plan
    payload = {
        "fidelity": "arranged",
        "difficulty": 3,
        "texturePlan": '[{"startBar": 0, "endBar": 4, "texture": "block"}, {"startBar": 4, "endBar": 8, "texture": "half"}]',
    }
    req = validate_request(payload)
    assert isinstance(req.texture_plan, TexturePlan)
    assert req.texture_plan.texture_for_bar(0) == TextureName.BLOCK
    assert req.texture_plan.texture_for_bar(3) == TextureName.BLOCK
    assert req.texture_plan.texture_for_bar(4) == TextureName.HALF
    assert req.texture_plan.texture_for_bar(7) == TextureName.HALF
    assert req.texture_plan.default_texture == TextureName.HALF


def test_texture_plan_rejects_invalid_json() -> None:
    import pytest

    from core.errors import ValidationError
    from services.score.service import validate_request

    payload = {
        "fidelity": "arranged",
        "difficulty": 3,
        "texturePlan": "not valid json",
    }
    with pytest.raises(ValidationError, match="invalid texturePlan JSON"):
        validate_request(payload)


def test_texture_plan_rejects_non_array() -> None:
    import pytest

    from core.errors import ValidationError
    from services.score.service import validate_request

    payload = {
        "fidelity": "arranged",
        "difficulty": 3,
        "texturePlan": '"not an array"',
    }
    with pytest.raises(ValidationError, match="texturePlan must be a JSON array"):
        validate_request(payload)


def test_texture_plan_rejects_invalid_section() -> None:
    import pytest

    from core.errors import ValidationError
    from services.score.service import validate_request

    payload = {
        "fidelity": "arranged",
        "difficulty": 3,
        "texturePlan": '[{"startBar": 0, "endBar": 4}]',  # missing texture
    }
    with pytest.raises(ValidationError, match="each texturePlan section must have a texture"):
        validate_request(payload)


def test_texture_plan_rejects_unknown_texture() -> None:
    import pytest

    from core.errors import ValidationError
    from services.score.service import validate_request

    payload = {
        "fidelity": "arranged",
        "difficulty": 3,
        "texturePlan": '[{"startBar": 0, "endBar": 4, "texture": "invalid"}]',
    }
    with pytest.raises(ValidationError, match="unsupported texture in texturePlan"):
        validate_request(payload)
