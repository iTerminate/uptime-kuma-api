from enum import Enum


class MonitorType(str, Enum):
    """Enumerate monitor types."""
    
    HTTP = "http"
    """HTTP(s)"""

    KEYWORD = "keyword"
    """HTTP(s) - Keyword"""

    PORT = "port"
    """TCP Port"""

    PING = "ping"
    """Ping"""

    DNS = "dns"
    """DNS"""

    DOCKER = "docker"
    """Docker Container"""

    SYSTEM_SERVICE = "system-service"
    """System Service"""

    REAL_BROWSER = "real-browser"
    """HTTP(s) - Browser Engine (Chrome/Chromium)"""

    GROUP = "group"
    """Group"""

    PUSH = "push"
    """Push"""

    MANUAL = "manual"
    """Manual"""

    GLOBALPING = "globalping"
    """Globalping"""

    GRPC_KEYWORD = "grpc-keyword"
    """gRPC(s) - Keyword"""

    JSON_QUERY = "json-query"
    """HTTP(s) - Json Query"""

    KAFKA_PRODUCER = "kafka-producer"
    """Kafka Producer"""

    MQTT = "mqtt"
    """MQTT"""

    RABBITMQ = "rabbitmq"
    """RabbitMQ"""

    SIP_OPTIONS = "sip-options"
    """SIP OPTIONS Ping"""

    SMTP = "smtp"
    """SMTP"""

    SNMP = "snmp"
    """SNMP"""

    TAILSCALE_PING = "tailscale-ping"
    """Tailscale Ping"""

    WEBSOCKET_UPGRADE = "websocket-upgrade"
    """WebSocket Upgrade"""

    SQLSERVER = "sqlserver"
    """Microsoft SQL Server"""

    MONGODB = "mongodb"
    """MongoDB"""

    MYSQL = "mysql"
    """MySQL/MariaDB"""

    ORACLEDB = "oracledb"
    """Oracle Database"""

    POSTGRES = "postgres"
    """PostgreSQL"""

    RADIUS = "radius"
    """Radius"""

    REDIS = "redis"
    """Redis"""

    GAMEDIG = "gamedig"
    """GameDig"""

    STEAM = "steam"
    """Steam Game Server"""
