"""Retry only the known Docker boot-time address-binding failure."""
import json
import subprocess


def needs_recovery(state):
    return (state.get("Status") == "exited"
            and "cannot assign requested address" in state.get("Error", ""))


def main():
    name = "myfitnesspal-mcp-gateway-1"
    result = subprocess.run(
        ["/usr/bin/docker", "inspect", "--format", "{{json .State}}", name],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode:
        return  # Docker/container may not exist yet; the timer tries later.
    if needs_recovery(json.loads(result.stdout)):
        # Do not restart healthy containers or intentionally stopped services.
        subprocess.run(["/usr/bin/docker", "start", name], check=True, timeout=30)
    # A failed Docker boot bind can also leave a running container detached
    # from every network, with no published ports, even after a restart.
    result = subprocess.run(
        ["/usr/bin/docker", "inspect", name],
        capture_output=True, text=True, check=True, timeout=15,
    )
    container = json.loads(result.stdout)[0]
    if (container["State"]["Running"]
            and not container["NetworkSettings"]["Networks"]):
        subprocess.run([
            "/usr/bin/docker", "network", "connect", "--alias", "gateway",
            "myfitnesspal-mcp_backend", name,
        ], check=True, timeout=30)


if __name__ == "__main__":
    main()
