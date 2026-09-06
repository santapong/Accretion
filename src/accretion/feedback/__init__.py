"""The v0.4 feedback layer (SDD §9.6, §9.7): independent verification results, the
experience projection with attribution, and the failure taxonomy with its recovery guard.
Every module here is a pure service over the sealed ``accretion.contracts.routing`` models;
``feedback.service`` (M3b) composes them into the ``FeedbackPipeline`` the run manager calls."""
