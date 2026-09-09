# Build context comes exclusively from prepare_worker_image.py. Runtime launches
# use the resulting immutable image ID, never this Dockerfile or a moving tag.
FROM python@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

# The base's own dated Debian snapshot is kept fixed. Expiry is ignored only
# for this authenticated historic snapshot; package signatures remain required.
RUN printf '%s\n' \
    'Types: deb' \
    'URIs: https://snapshot.debian.org/archive/debian/20260623T000000Z/' \
    'Suites: trixie trixie-updates' \
    'Components: main' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
    'Check-Valid-Until: no' > /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends libegl1 libgl1 libgl1-mesa-dri \
    && mkdir -p /opt/accretion/provenance \
    && dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' \
       > /opt/accretion/provenance/debian-packages.tsv \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/accretion/provenance/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    -r /opt/accretion/provenance/requirements.txt \
    && python -m pip freeze --all > /opt/accretion/provenance/python-packages.txt
COPY src/ /opt/accretion/src/
COPY models/ /opt/accretion/models/
COPY LICENSE /opt/accretion/LICENSE
COPY source-manifest.json /opt/accretion/provenance/source-manifest.json
COPY --chmod=0555 simulation-worker /opt/accretion/bin/simulation-worker
RUN chmod -R a-w /opt/accretion
ENV PYTHONPATH=/opt/accretion/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["/opt/accretion/bin/simulation-worker"]
