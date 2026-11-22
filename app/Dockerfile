# PoB engine + FastAPI in one image. Build context is the repo root:
#   docker compose -f app/docker-compose.yml up --build

# --- toolchain: LuaJIT at the commit upstream CI pins, plus native Lua modules ---
FROM python:3.13-slim-bookworm AS toolchain
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git curl unzip ca-certificates \
	&& rm -rf /var/lib/apt/lists/*
WORKDIR /build
ARG LUAJIT_COMMIT=2460b3ff93a1c955de3d62cfc825de7d68dc272e
RUN git clone https://github.com/LuaJIT/LuaJIT && cd LuaJIT && git checkout ${LUAJIT_COMMIT} \
	&& make -j"$(nproc)" PREFIX=/opt/luajit && make install PREFIX=/opt/luajit \
	&& ln -sf "$(ls /opt/luajit/bin/luajit-2.1.* | head -1)" /opt/luajit/bin/luajit
ARG LUAROCKS_VERSION=3.11.1
RUN curl -fsSL https://luarocks.org/releases/luarocks-${LUAROCKS_VERSION}.tar.gz | tar xz \
	&& cd luarocks-${LUAROCKS_VERSION} \
	&& ./configure --with-lua=/opt/luajit --with-lua-include=/opt/luajit/include/luajit-2.1 --lua-version=5.1 \
	&& make && make install
RUN for rock in luautf8 luafilesystem; do \
		luarocks --tree=/opt/rocks --lua-version=5.1 install $rock; \
	done

# --- python deps ---
FROM python:3.13-slim-bookworm AS pydeps
COPY app/api/requirements.txt app/api/requirements-dev.txt /tmp/
ARG INSTALL_DEV=false
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt \
	&& if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir --prefix=/install -r /tmp/requirements-dev.txt; fi

# --- runtime ---
FROM python:3.13-slim-bookworm
COPY --from=toolchain /opt/luajit /opt/luajit
COPY --from=toolchain /opt/rocks /opt/rocks
COPY --from=pydeps /install /usr/local
ENV POB_LUAJIT=/opt/luajit/bin/luajit \
	POB_ROCKS=/opt/rocks \
	PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 pob
WORKDIR /pob
# Only what the headless engine and API need (see app/Dockerfile.dockerignore).
COPY --chown=pob runtime/lua runtime/lua
COPY --chown=pob src src
COPY --chown=pob spec/TestBuilds spec/TestBuilds
COPY --chown=pob app/engine app/engine
COPY --chown=pob app/api app/api

USER pob
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
	CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "main:app", "--app-dir", "app/api", "--host", "0.0.0.0", "--port", "8000"]
