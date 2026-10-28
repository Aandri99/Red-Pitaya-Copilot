#include <arpa/inet.h>
#include <cerrno>
#include <csignal>
#include <cstring>
#include <ctime>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

namespace {
constexpr std::size_t kMaxRequestBytes = 8192;
volatile std::sig_atomic_t shutdown_requested = 0;
const char *kVersion = "__APP_VERSION__";

void request_shutdown(int) { shutdown_requested = 1; }

std::string utc_now() {
    std::time_t now = std::time(nullptr);
    std::tm value {};
    gmtime_r(&now, &value);
    char buffer[32] {};
    std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &value);
    return buffer;
}

std::string status_json() {
    std::ostringstream stream;
    stream << "{\"ok\":true,\"status\":{"
           << "\"service\":\"integration_required\","
           << "\"hardware_ready\":false,"
           << "\"safe_state\":null,"
           << "\"stop_implemented\":false,"
           << "\"version\":\"" << kVersion << "\","
           << "\"server_time\":\"" << utc_now() << "\"}}";
    return stream.str();
}

std::string error_json(const char *code, const char *message) {
    std::ostringstream stream;
    stream << "{\"ok\":false,\"error\":{"
           << "\"code\":\"" << code << "\","
           << "\"message\":\"" << message << "\"}}";
    return stream.str();
}

void send_all(int socket_fd, const std::string &data) {
    std::size_t offset = 0;
    while (offset < data.size()) {
        const ssize_t sent = ::send(
            socket_fd, data.data() + offset, data.size() - offset, MSG_NOSIGNAL
        );
        if (sent < 0) {
            if (errno == EINTR) continue;
            return;
        }
        if (sent == 0) return;
        offset += static_cast<std::size_t>(sent);
    }
}

void respond(int socket_fd, int status, const char *reason, const std::string &body) {
    std::ostringstream response;
    response << "HTTP/1.1 " << status << " " << reason << "\r\n"
             << "Content-Type: application/json; charset=utf-8\r\n"
             << "Cache-Control: no-store\r\n"
             << "X-Content-Type-Options: nosniff\r\n"
             << "Connection: close\r\n"
             << "Content-Length: " << body.size() << "\r\n\r\n"
             << body;
    send_all(socket_fd, response.str());
}

void handle_client(int client_fd) {
    std::string request;
    request.resize(kMaxRequestBytes);
    const ssize_t count = ::recv(client_fd, request.data(), request.size(), 0);
    if (count <= 0) return;
    request.resize(static_cast<std::size_t>(count));
    if (request.size() == kMaxRequestBytes) {
        respond(client_fd, 413, "Payload Too Large",
                error_json("request_too_large", "Request is too large"));
        return;
    }

    const std::size_t line_end = request.find("\r\n");
    if (line_end == std::string::npos) {
        respond(client_fd, 400, "Bad Request",
                error_json("invalid_request", "Malformed request line"));
        return;
    }

    std::istringstream line(request.substr(0, line_end));
    std::string method;
    std::string path;
    std::string protocol;
    line >> method >> path >> protocol;
    if (method.empty() || path.empty() || protocol.rfind("HTTP/", 0) != 0) {
        respond(client_fd, 400, "Bad Request",
                error_json("invalid_request", "Malformed request line"));
        return;
    }

    if (method == "GET" && (path == "/status" || path == "/version")) {
        respond(client_fd, 200, "OK", status_json());
        return;
    }
    if (method == "POST" && path == "/stop") {
        // SCAFFOLD_HARDWARE_INTEGRATION_REQUIRED
        // Replace this rejection only after implementing and testing the sole
        // resource owner's idempotent safe-stop operation.
        respond(client_fd, 501, "Not Implemented",
                error_json("integration_required",
                           "Safe-stop hardware integration is not implemented"));
        return;
    }
    if (path == "/status" || path == "/version" || path == "/stop") {
        respond(client_fd, 405, "Method Not Allowed",
                error_json("method_not_allowed", "Method not allowed"));
        return;
    }
    respond(client_fd, 404, "Not Found",
            error_json("not_found", "Unknown endpoint"));
}

int parse_port(const char *text) {
    try {
        std::size_t used = 0;
        const int port = std::stoi(text, &used);
        if (used != std::strlen(text) || port < 1024 || port > 65535) return -1;
        return port;
    } catch (...) {
        return -1;
    }
}
}  // namespace

int main(int argc, char **argv) {
    int port = __SERVICE_PORT__;
    if (argc == 3 && std::string(argv[1]) == "--port") {
        port = parse_port(argv[2]);
    } else if (argc != 1) {
        std::cerr << "usage: " << argv[0] << " [--port PORT]\n";
        return 2;
    }
    if (port < 0) {
        std::cerr << "port must be between 1024 and 65535\n";
        return 2;
    }

    std::signal(SIGINT, request_shutdown);
    std::signal(SIGTERM, request_shutdown);
    std::signal(SIGPIPE, SIG_IGN);

    const int server_fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) {
        std::cerr << "socket failed: " << std::strerror(errno) << "\n";
        return 1;
    }
    int reuse = 1;
    ::setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    sockaddr_in address {};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(static_cast<uint16_t>(port));
    if (::bind(server_fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
        std::cerr << "bind failed: " << std::strerror(errno) << "\n";
        ::close(server_fd);
        return 1;
    }
    if (::listen(server_fd, 8) < 0) {
        std::cerr << "listen failed: " << std::strerror(errno) << "\n";
        ::close(server_fd);
        return 1;
    }

    std::cout << "{\"event\":\"service_started\",\"port\":" << port
              << ",\"time\":\"" << utc_now() << "\"}\n";
    while (!shutdown_requested) {
        fd_set read_set;
        FD_ZERO(&read_set);
        FD_SET(server_fd, &read_set);
        timeval timeout {0, 500000};
        const int selected = ::select(server_fd + 1, &read_set, nullptr, nullptr, &timeout);
        if (selected < 0) {
            if (errno == EINTR) continue;
            std::cerr << "select failed: " << std::strerror(errno) << "\n";
            break;
        }
        if (selected == 0) continue;

        const int client_fd = ::accept(server_fd, nullptr, nullptr);
        if (client_fd < 0) {
            if (errno == EINTR) continue;
            std::cerr << "accept failed: " << std::strerror(errno) << "\n";
            continue;
        }
        handle_client(client_fd);
        ::close(client_fd);
    }

    ::close(server_fd);
    std::cout << "{\"event\":\"service_stopped\",\"safe_state\":null,"
              << "\"time\":\"" << utc_now() << "\"}\n";
    return 0;
}
