from app.tasks import _requires_unverified_french


def test_french_requirement_patterns_require_owner_confirmation():
    assert _requires_unverified_french("Fluency in French and English is required")
    assert _requires_unverified_french("Must be fluent in both French and English")
    assert _requires_unverified_french("This is a bilingual role")
    assert not _requires_unverified_french("Excellent written communication skills")
