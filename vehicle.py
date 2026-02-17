import secrets
from datetime import datetime, timedelta
from typing import List

import googlemaps
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext

# -------------------- DATABASE CONFIG -----------------
DATABASE_URL = "mysql+pymysql://root:@127.0.0.1/vehicle_project"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

# -------------------- FASTAPI APP --------------------
app = FastAPI(title="Vehicle Management System")

@app.get("/")
def root():
    return {
        "message": "Vehicle Management System API",
        "docs": "/docs",
        "redoc": "/redoc"
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------- GOOGLE MAPS --------------------
GOOGLE_MAPS_API_KEY = "YOUR_GOOGLE_MAPS_API_KEY"

try:
    gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
except (ValueError, Exception) as e:
    print(f"WARNING: Google Maps API initialization failed ({e}). Distance calculation will use a fallback value.")
    gmaps = None

def calculate_distance_km(pickup: str, drop: str):
    if gmaps is None or GOOGLE_MAPS_API_KEY == "YOUR_GOOGLE_MAPS_API_KEY":
        # Return a dummy distance for development/testing if API key is invalid
        return 15.0
    
    try:
        result = gmaps.distance_matrix(
            origins=pickup,
            destinations=drop,
            mode="driving"
        )
        meters = result["rows"][0]["elements"][0]["distance"]["value"]
        return meters / 1000
    except Exception as e:
        print(f"Error calculating distance: {e}")
        return 15.0

# -------------------- LOCATION PRICING --------------------
LOCATION_MULTIPLIER = {
    "chennai": 1.0,
    "bangalore": 1.2,
    "hyderabad": 1.1,
    "remote": 1.5
}

def get_location_multiplier(location: str):
    return LOCATION_MULTIPLIER.get(location.lower(), 1.0)

# -------------------- MODELS --------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False)

class Driver(Base):
    __tablename__ = "drivers"
    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    phone = Column(String(15))
    salary_per_km = Column(Float)
    location = Column(String(100))

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True)
    vehicle_number = Column(String(20), unique=True)
    type = Column(String(50))
    rate_per_km = Column(Float)
    location = Column(String(100))

class Trip(Base):
    __tablename__ = "trips"
    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, ForeignKey("drivers.id"))
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    pickup_location = Column(String(200))
    drop_location = Column(String(200))
    distance = Column(Float)
    total_amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    driver = relationship("Driver")
    vehicle = relationship("Vehicle")

Base.metadata.create_all(bind=engine)

# -------------------- AUTH CONFIG --------------------
SECRET_KEY = secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# -------------------- DB DEP --------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -------------------- SCHEMAS --------------------
class Token(BaseModel):
    access_token: str
    token_type: str

class RegisterSchema(BaseModel):
    username: str
    password: str
    role: str

class DriverSchema(BaseModel):
    name: str
    phone: str
    salary_per_km: float
    location: str
    class Config:
        orm_mode = True

class VehicleSchema(BaseModel):
    vehicle_number: str
    type: str
    rate_per_km: float
    location: str
    class Config:
        orm_mode = True

class TripCreate(BaseModel):
    driver_id: int
    vehicle_id: int
    pickup_location: str
    drop_location: str

class TripResponse(BaseModel):
    id: int
    driver_id: int
    vehicle_id: int
    pickup_location: str
    drop_location: str
    distance: float
    total_amount: float
    created_at: datetime
    class Config:
        orm_mode = True

# -------------------- AUTH UTILS --------------------
def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict):
    data.update({"exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)})
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_role(roles: List[str]):
    def checker(user=Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Access denied")
        return user
    return checker

# -------------------- AUTH ROUTES --------------------
@app.post("/register")
def register(data: RegisterSchema, db: Session = Depends(get_db)):
    user = User(
        username=data.username,
        password=hash_password(data.password),
        role=data.role
    )
    db.add(user)
    db.commit()
    return {"message": "User registered successfully"}

@app.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.username, "role": user.role})
    return {"access_token": token, "token_type": "bearer"}

# -------------------- DRIVER APIs --------------------
@app.post("/drivers", dependencies=[Depends(require_role(["admin", "manager"]))])
def create_driver(data: DriverSchema, db: Session = Depends(get_db)):
    d = Driver(**data.dict())
    db.add(d)
    db.commit()
    return d

@app.get("/drivers", dependencies=[Depends(require_role(["admin", "manager", "viewer"]))])
def get_drivers(db: Session = Depends(get_db)):
    return db.query(Driver).all()

# -------------------- VEHICLE APIs --------------------
@app.post("/vehicles", dependencies=[Depends(require_role(["admin", "manager"]))])
def create_vehicle(data: VehicleSchema, db: Session = Depends(get_db)):
    v = Vehicle(**data.dict())
    db.add(v)
    db.commit()
    return v

@app.get("/vehicles", dependencies=[Depends(require_role(["admin", "manager", "viewer"]))])
def get_vehicles(db: Session = Depends(get_db)):
    return db.query(Vehicle).all()

# -------------------- TRIP APIs --------------------
@app.post("/trips", response_model=TripResponse,
          dependencies=[Depends(require_role(["admin", "manager"]))])
def create_trip(data: TripCreate, db: Session = Depends(get_db)):

    driver = db.query(Driver).get(data.driver_id)
    vehicle = db.query(Vehicle).get(data.vehicle_id)

    if not driver or not vehicle:
        raise HTTPException(status_code=404, detail="Driver or Vehicle not found")

    distance = calculate_distance_km(data.pickup_location, data.drop_location)
    multiplier = get_location_multiplier(data.pickup_location)
    total_amount = distance * vehicle.rate_per_km * multiplier

    trip = Trip(
        driver_id=data.driver_id,
        vehicle_id=data.vehicle_id,
        pickup_location=data.pickup_location,
        drop_location=data.drop_location,
        distance=distance,
        total_amount=total_amount
    )

    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip

@app.get("/trips", dependencies=[Depends(require_role(["admin", "manager", "viewer"]))])
def get_trips(db: Session = Depends(get_db)):
    return db.query(Trip).all()

