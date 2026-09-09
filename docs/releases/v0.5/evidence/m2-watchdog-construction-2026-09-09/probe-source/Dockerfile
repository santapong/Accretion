FROM python@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf
COPY accretion /opt/accretion/probe/accretion
COPY --chmod=0555 probe.py /opt/accretion/bin/simulation-worker
LABEL dev.draveniq.accretion.scope="WATCHDOG_CONSTRUCTION_ONLY_NO_ROBOT"
