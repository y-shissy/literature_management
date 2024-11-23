from sqlalchemy import Column, Integer, String, Boolean, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

class Metadata(Base):
    __tablename__ = 'metadata'
    id = Column(Integer, primary_key=True, autoincrement=True)
    タイトル = Column(String)
    著者 = Column(String)
    ジャーナル = Column(String)
    巻 = Column(String)
    号 = Column(String)
    開始ページ = Column(String)
    終了ページ = Column(String)
    年 = Column(Integer)
    要約 = Column(String)
    キーワード = Column(String)
    カテゴリ = Column(String)
    doi = Column(String, nullable=False)
    doi_url = Column(String)
    ファイルリンク = Column(String)
    メモ = Column(String)
    Read = Column(Boolean, default=False)

DATABASE_URL = "sqlite:///metadata.db"
engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

def get_session():
    return SessionLocal()
