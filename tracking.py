from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from typing import Dict, List
import json

app = FastAPI(title="Live Vehicle Tracking")

# ---------------- CONNECTION MANAGER ----------------
class ConnectionManager:
    def __init__(self):
        # vehicle_id -> list of websockets
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, vehicle_id: int):
        await websocket.accept()
        if vehicle_id not in self.active_connections:
            self.active_connections[vehicle_id] = []
        self.active_connections[vehicle_id].append(websocket)
        print(f"New connection for Vehicle {vehicle_id}. Active: {len(self.active_connections[vehicle_id])}")

    def disconnect(self, websocket: WebSocket, vehicle_id: int):
        if vehicle_id in self.active_connections:
            if websocket in self.active_connections[vehicle_id]:
                self.active_connections[vehicle_id].remove(websocket)
            if not self.active_connections[vehicle_id]:
                del self.active_connections[vehicle_id]
        print(f"Vehicle {vehicle_id} disconnected.")

    async def broadcast(self, vehicle_id: int, message: str):
        if vehicle_id in self.active_connections:
            for connection in self.active_connections[vehicle_id]:
                try:
                    await connection.send_text(message)
                except Exception as e:
                    print(f"Error broadcasting to a connection: {e}")

manager = ConnectionManager()
vehicle_locations = {}

# ---------------- HOME PAGE (MAP VIEW) ----------------
@app.get("/", response_class=HTMLResponse)
def home():
    return map_view()

# ---------------- GPS SENDER (DRIVER) ----------------
@app.get("/send", response_class=HTMLResponse)
def gps_sender():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>GPS Sender</title>
</head>
<body>
    <div style="text-align:center; margin-top:50px; font-family:sans-serif;">
        <h2 style="color:#2c3e50;">GPS Sender (Driver)</h2>
        <div id="status" style="padding:10px; margin:20px; border-radius:5px; background:#ecf0f1;">Connecting...</div>
        <p id="coords">Waiting for coordinates...</p>
    </div>

    <script>
        const vehicleId = 1;
        let ws;
        const statusDiv = document.getElementById('status');
        const coordsDiv = document.getElementById('coords');

        function connect() {
            ws = new WebSocket("ws://" + window.location.host + "/ws/location/" + vehicleId);
            
            ws.onopen = () => {
                statusDiv.innerText = "Connected - Sending GPS";
                statusDiv.style.background = "#d4edda";
                
                navigator.geolocation.watchPosition(pos => {
                    const data = {
                        lat: pos.coords.latitude,
                        lng: pos.coords.longitude
                    };
                    coordsDiv.innerText = `Lat: ${data.lat}, Lng: ${data.lng}`;
                    if(ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify(data));
                    }
                }, err => {
                    coordsDiv.innerText = "Error: " + err.message;
                }, { enableHighAccuracy: true });
            };

            ws.onclose = () => {
                statusDiv.innerText = "Disconnected - Reconnecting...";
                statusDiv.style.background = "#f8d7da";
                setTimeout(connect, 3000);
            };
        }

        connect();
    </script>
</body>
</html>
"""

# ---------------- MAP VIEW (CLIENT) ----------------
@app.get("/map", response_class=HTMLResponse)
def map_view():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Live Vehicle Tracking</title>
    <style>
        #map { height: 100vh; width: 100%; }
        #info { 
            position: fixed; top: 10px; left: 10px; z-index: 1000; 
            background: white; padding: 10px; border-radius: 5px; 
            box-shadow: 0 2px 5px rgba(0,0,0,0.2); font-family: sans-serif;
        }
    </style>
</head>
<body>
    <div id="info">Vehicle 1: Waiting for updates...</div>
    <div id="map"></div>

    <script src="https://maps.googleapis.com/maps/api/js?key=AIzaSyBY9-C6yjKmFvPRCXZdkM1sURJfM6RqBeM" async defer></script>
    <script>
        let map, marker;
        const infoDiv = document.getElementById('info');

        function initMap() {
            map = new google.maps.Map(document.getElementById("map"), {
                zoom: 15,
                center: { lat: 13.0827, lng: 80.2707 }
            });

            marker = new google.maps.Marker({
                map: map,
                position: { lat: 13.0827, lng: 80.2707 },
                title: "Vehicle 1"
            });

            connect();
        }

        function connect() {
            const ws = new WebSocket("ws://" + window.location.host + "/ws/location/1");

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                const pos = { lat: data.lat, lng: data.lng };
                marker.setPosition(pos);
                map.panTo(pos);
                infoDiv.innerText = `Vehicle 1: Updated at ${new Date().toLocaleTimeString()}`;
            };

            ws.onclose = () => {
                infoDiv.innerText = "Connection lost. Reconnecting...";
                setTimeout(connect, 3000);
            };
        }

        window.onload = initMap;
    </script>
</body>
</html>
"""

# ---------------- WEBSOCKET ----------------
@app.websocket("/ws/location/{vehicle_id}")
async def websocket_location(websocket: WebSocket, vehicle_id: int):
    await manager.connect(websocket, vehicle_id)
    try:
        while True:
            # Wait for data from the driver
            data = await websocket.receive_text()
            location = json.loads(data)
            vehicle_locations[vehicle_id] = location
            
            # Broadcast to ALL clients (including the sender and any maps)
            await manager.broadcast(vehicle_id, json.dumps(location))
    except WebSocketDisconnect:
        manager.disconnect(websocket, vehicle_id)
    except Exception as e:
        print(f"Error in websocket loop: {e}")
        manager.disconnect(websocket, vehicle_id)
