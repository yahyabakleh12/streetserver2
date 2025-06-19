# db.py

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ─── Database connection ───
# `DATABASE_URL` must be supplied by the environment.  No credentials are
# stored in the repository.
DATABASE_URL = os.environ["DATABASE_URL"]

# Create engine with pool_pre_ping so that any stale connection is auto‐replaced,
# pool_recycle so we don’t hold sockets open past typical wait_timeout,
# and expire_on_commit=False so attribute access after commit doesn’t immediately reload.
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping    = True,     # ping MySQL before checkout
    pool_recycle     = 1800,     # recycle connections older than 30 minutes
    pool_size        = 10,       # keep up to 10 open connections
    max_overflow     = 20,       # allow bursts up to 30 total
    pool_timeout     = 30,       # wait up to 30s for a connection
)

Base = declarative_base()

# We set expire_on_commit=False so that after commit our objects do not expire
SessionLocal = sessionmaker(
    bind            = engine,
    autoflush       = False,
    autocommit      = False,
    expire_on_commit=False
)
