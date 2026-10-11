"""BounceProcessor NDR 검색식 — 비ASCII SUBJECT 는 CHARSET UTF-8 literal 로 전송"""

from src.common.delivery.bounce_processor import BounceProcessor


class _FakeIMAP:
    def __init__(self):
        self.literal = None
        self.calls = []

    def uid(self, command, *args):
        # 실제 imaplib 은 명령행을 ASCII 로 인코딩 — 비ASCII 가 섞이면 실패해야 한다
        " ".join(a for a in args if a).encode("ascii")
        self.calls.append((args, self.literal))
        self.literal = None
        return "OK", [b"%d" % len(self.calls)]


def test_non_ascii_subject_uses_utf8_literal():
    imap = _FakeIMAP()
    uids = BounceProcessor()._search_ndr_uids(imap, since_days=7)

    assert len(uids) == len(BounceProcessor.NDR_SEARCH_CRITERIA)
    args, literal = imap.calls[-1]
    assert args[:2] == ("CHARSET", "UTF-8")
    assert args[-1] == "SUBJECT"
    assert literal == "주소를 찾을 수 없".encode("utf-8")


def test_ascii_criteria_send_without_literal():
    imap = _FakeIMAP()
    BounceProcessor()._search_ndr_uids(imap, since_days=7)

    for args, literal in imap.calls[:-1]:
        assert args[0] is None
        assert literal is None
