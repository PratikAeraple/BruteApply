import pytest

from app.domain.profile import merge_profile_facts, validate_profile_facts


def test_profile_values_are_bounded_and_keys_are_safe():
    assert validate_profile_facts({"full_name": "  Demo Owner  ", "skills": ""}) == {
        "full_name": "Demo Owner", "skills": ""
    }
    with pytest.raises(ValueError):
        validate_profile_facts({"../private": "value"})
    with pytest.raises(ValueError):
        validate_profile_facts({"skills": "x" * 5001})


def test_edit_resets_value_and_empty_value_removes_fact():
    assert merge_profile_facts({"skills": "Linux", "city": "Toronto"},
                               {"skills": "Linux and Docker", "city": ""}) == {
        "skills": "Linux and Docker"
    }
