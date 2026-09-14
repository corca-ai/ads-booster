from datetime import UTC, datetime

from ads_booster.contracts.threads import ThreadsPost
from ads_booster.providers.threads_api import ThreadsPage
from ads_booster.tools.threads_tools import _observed_posts


def test_observed_posts_preserves_page_contract() -> None:
    # Given
    page = ThreadsPage(
        posts=(
            ThreadsPost(
                post_id="post",
                account_id="account",
                username="trace",
                text="hello",
                permalink="https://threads.net/post",
                media_type="TEXT_POST",
                timestamp=datetime(2026, 9, 15, tzinfo=UTC),
            ),
        ),
        after="next-page",
    )

    # When
    result = _observed_posts(page)

    # Then
    assert result.output == {
        "posts": [page.posts[0].model_dump(mode="json")],
        "after": "next-page",
    }
