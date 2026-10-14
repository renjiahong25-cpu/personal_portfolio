"""Tests for choosing the transcription model.

The pipeline used to construct :class:`BasicPitchTranscriber` inline with no way to ask for
anything else. It now picks between two models, and the interesting part is not that both
work - it is what happens when the caller asks for one that is not installed. The two
models disagree about note duration by more than threefold on the same audio
(median 0.383 s against 0.386 s, but ByteDance's pedal-amplified rule put 29 % of its notes
at exactly the search cap), so quietly substituting one for the other would return a score
with the defect the caller asked to avoid and say nothing about it.
"""

from __future__ import annotations

import pytest

from core.config import Settings
from core.errors import ValidationError
from services.audio_analysis.polyphony import BasicPitchTranscriber, Transcriber
from services.audio_analysis.transkun import TranskunTranscriber
from services.score.service import (
    DEFAULT_TRANSCRIBER,
    TRANSCRIBER_HINTS,
    TRANSCRIBER_NAMES,
    ScoreRequest,
    _build_transcriber,
    validate_request,
)


class TestNames:
    def test_both_models_are_offered(self):
        assert set(TRANSCRIBER_NAMES) == {"basic_pitch", "transkun"}

    def test_the_fast_one_is_the_default(self):
        # basic-pitch transcribes 239.5 s in 2.3 s where transkun takes 87.8 s. The
        # default has to be the one a request can afford.
        assert DEFAULT_TRANSCRIBER == "basic_pitch"

    def test_every_offered_model_has_an_explanation(self):
        assert set(TRANSCRIBER_HINTS) == set(TRANSCRIBER_NAMES)
        assert all(hint.strip() for hint in TRANSCRIBER_HINTS.values())


class TestProtocol:
    """The two implementations share no base class; the protocol is the whole contract."""

    def test_basic_pitch_satisfies_it(self):
        assert isinstance(BasicPitchTranscriber(), Transcriber)

    def test_transkun_satisfies_it(self):
        assert isinstance(TranskunTranscriber(), Transcriber)


class TestBuild:
    def test_basic_pitch_needs_nothing_installed(self):
        assert isinstance(_build_transcriber("basic_pitch"), BasicPitchTranscriber)

    def test_transkun_is_built_when_available(self):
        assert isinstance(_build_transcriber("transkun"), TranskunTranscriber)

    def test_an_unknown_name_is_refused_with_the_supported_list(self):
        with pytest.raises(ValidationError) as caught:
            _build_transcriber("magenta")
        assert caught.value.details["supported"] == list(TRANSCRIBER_NAMES)

    def test_a_missing_transkun_says_so_instead_of_falling_back(self, monkeypatch, tmp_path):
        # The behaviour that matters: no silent substitution.
        monkeypatch.setenv("PIANOFLOW_TRANSKUN_PYTHON", str(tmp_path / "absent.exe"))
        with pytest.raises(ValidationError) as caught:
            _build_transcriber("transkun")
        assert "not installed" in caught.value.message
        assert "PIANOFLOW_TRANSKUN_PYTHON" in caught.value.details["hint"]

    def test_the_configured_interpreter_is_preferred(self, tmp_path):
        interpreter = tmp_path / "python.exe"
        interpreter.write_text("", encoding="utf-8")
        settings = Settings(transkun_python=str(interpreter))
        transcriber = _build_transcriber("transkun", settings)
        assert transcriber._python == interpreter

    def test_settings_default_to_basic_pitch(self):
        assert Settings().transcriber == DEFAULT_TRANSCRIBER


class TestRequestValidation:
    def test_the_default_is_the_fast_model(self):
        assert validate_request({}).transcriber == DEFAULT_TRANSCRIBER

    def test_transkun_can_be_asked_for(self):
        assert validate_request({"transcriber": "transkun"}).transcriber == "transkun"

    def test_the_name_is_case_and_space_insensitive(self):
        assert validate_request({"transcriber": "  TransKun  "}).transcriber == "transkun"

    def test_an_unknown_model_is_refused(self):
        with pytest.raises(ValidationError, match="unsupported transcriber"):
            validate_request({"transcriber": "magenta"})

    def test_an_empty_value_means_the_default(self):
        assert validate_request({"transcriber": ""}).transcriber == DEFAULT_TRANSCRIBER

    def test_the_model_survives_into_the_serialised_request(self):
        assert validate_request({"transcriber": "transkun"}).to_dict()["transcriber"] == (
            "transkun"
        )

    def test_a_non_string_model_is_refused(self):
        # str() of a number would become a nonsense name that then fails the lookup, but
        # the message should name the field the way the other fields do.
        with pytest.raises(ValidationError, match="unsupported transcriber"):
            validate_request({"transcriber": 7})


class TestRequestDefault:
    def test_a_hand_built_request_gets_the_default_model(self):
        assert ScoreRequest().transcriber == DEFAULT_TRANSCRIBER

    def test_the_response_reports_which_model_was_used(self):
        # Traced, not hidden: the two models produce different note lengths for the same
        # audio, so a score has to say where its notes came from.
        assert ScoreRequest(transcriber="transkun").to_dict()["transcriber"] == "transkun"


class TestUploadPathCarriesTheModel:
    """The upload endpoints read options from multipart fields, not JSON.

    Each module keeps an explicit whitelist (``option_fields``), so a field the JSON path
    honours and the upload path omits is the worst kind of bug: the upload runs a different
    model than the user chose and still reports success. Both modules have to carry
    ``transcriber``.
    """

    def test_both_upload_whitelists_include_transcriber(self):
        from api.handlers.score import (
            ScoreArrangeUploadHandler,
            ScoreTranscribeUploadHandler,
        )

        assert "transcriber" in ScoreTranscribeUploadHandler.option_fields
        assert "transcriber" in ScoreArrangeUploadHandler.option_fields

    def test_the_shared_core_fields_are_on_both_whitelists(self):
        # validate_request reads fidelity, difficulty, title and previewSeconds for every
        # request. Anything a module's JSON path honours and its upload omits is a silent
        # divergence, so the core fields must be whitelisted on both uploads.
        from api.handlers.score import (
            ScoreArrangeUploadHandler,
            ScoreTranscribeUploadHandler,
        )

        for field in ("fidelity", "difficulty", "title", "previewSeconds"):
            assert field in ScoreTranscribeUploadHandler.option_fields, field
            assert field in ScoreArrangeUploadHandler.option_fields, field

    def test_module_owned_fields_follow_their_module(self):
        # fillChords only acts in the transcribed mode, so it is owned by the transcribe
        # upload; pattern/texture are owned by the arrange upload. Crossing the line is a
        # 400 on the JSON path, so the uploads must not offer the option at all.
        from api.handlers.score import (
            ScoreArrangeUploadHandler,
            ScoreTranscribeUploadHandler,
        )

        assert "fillChords" in ScoreTranscribeUploadHandler.option_fields
        assert "fillChords" not in ScoreArrangeUploadHandler.option_fields
        for field in ("pattern", "texture", "texturePlan", "accompaniment"):
            assert field in ScoreArrangeUploadHandler.option_fields, field
            assert field not in ScoreTranscribeUploadHandler.option_fields, field
