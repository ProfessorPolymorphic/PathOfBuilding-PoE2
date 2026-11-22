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
# Native modules, compiled directly from pinned releases (same versions setup.sh
# gets on macOS). luarocks can't be used here: running on this LuaJIT it fails to
# load the luarocks.org manifest ("more than 65536 constants").
ARG LUAUTF8_VERSION=0.3.0
ARG LFS_TAG=v1_9_0
ENV LUA_INC=/opt/luajit/include/luajit-2.1 ROCKS_LIB=/opt/rocks/lib/lua/5.1
RUN mkdir -p ${ROCKS_LIB} \
	&& curl -fsSL https://github.com/starwing/luautf8/archive/refs/tags/${LUAUTF8_VERSION}.tar.gz | tar xz \
	&& gcc -O2 -fPIC -shared -I${LUA_INC} -o ${ROCKS_LIB}/lua-utf8.so luautf8-${LUAUTF8_VERSION}/lutf8lib.c \
	&& curl -fsSL https://github.com/lunarmodules/luafilesystem/archive/refs/tags/${LFS_TAG}.tar.gz | tar xz \
	&& gcc -O2 -fPIC -shared -I${LUA_INC} -o ${ROCKS_LIB}/lfs.so luafilesystem-*/src/lfs.c \
	&& /opt/luajit/bin/luajit -e 'package.cpath="/opt/rocks/lib/lua/5.1/?.so"; require("lua-utf8"); require("lfs"); print("native modules OK")'

# --- web app ---
FROM node:22-slim AS web
WORKDIR /web
COPY app/web/package.json app/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app/web/ ./
RUN npm run build

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

RUN useradd --create-home --uid 1000 pob \
	&& mkdir -p /data/builds && chown -R pob /data
ENV POB_STATE_DIR=/data/builds
WORKDIR /pob
# Only what the headless engine and API need (see app/Dockerfile.dockerignore).
COPY --chown=pob runtime/lua runtime/lua
COPY --chown=pob src src
COPY --chown=pob app/engine app/engine
COPY --chown=pob app/api app/api
COPY --from=web --chown=pob /web/dist app/web/dist

USER pob
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
	CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "main:app", "--app-dir", "app/api", "--host", "0.0.0.0", "--port", "8000"]
