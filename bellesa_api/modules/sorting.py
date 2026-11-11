from enum import StrEnum


class Sort(StrEnum):
    RECENT = "recent"
    POPULAR = "popular"
    LONGEST = "longest"
    RELEVANCE = "relevance"


ORDER_FIELDS = {
    Sort.RECENT: "posted_on",
    Sort.POPULAR: "views",
    Sort.LONGEST: "duration",
    Sort.RELEVANCE: "relevance",
}
