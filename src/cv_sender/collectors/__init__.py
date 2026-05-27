"""Job offer collectors — public interface."""

from cv_sender.collectors.base import (
    BaseJobCollector,
    CollectedOffer,
    JobCollectionResult,
    JobSearchCriteria,
)
from cv_sender.collectors.justjoin import JustJoinCollector
from cv_sender.collectors.nofluffjobs import NoFluffJobsCollector
from cv_sender.collectors.pracuj import PracujCollector
from cv_sender.collectors.rocketjobs import RocketJobsCollector
from cv_sender.collectors.linkedin import LinkedInCollector

__all__ = [
    "BaseJobCollector",
    "CollectedOffer",
    "JobCollectionResult",
    "JobSearchCriteria",
    "JustJoinCollector",
    "NoFluffJobsCollector",
    "PracujCollector",
    "RocketJobsCollector",
    "LinkedInCollector",
]
