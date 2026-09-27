//! The HTTP side of the engine's own reading: one agent per stream (so its
//! connections are reused across range requests), a probe, and ranged GETs.

use std::io::Read;
use std::net::ToSocketAddrs;
use std::time::Duration;

use ureq::ResponseExt as _;
use ureq::http::{StatusCode, Uri};
use ureq::tls::{RootCerts, TlsConfig};

/// How long a connection may take to open. Short: a host that will not
/// answer should free its worker, and the player's own open watchdog is 15 s.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
/// How long the host may take to start answering a request.
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(30);
/// The least a body is allowed per second before its request counts as
/// stalled: `ureq`'s body timeout is one budget for the whole body, so it is
/// sized to what is asked for (see `Client::range`).
const SLOWEST_BYTES_PER_S: u64 = 256 * 1024;
/// A `Retry-After` longer than this is not a pause but a different plan.
const MAX_RETRY_AFTER: Duration = Duration::from_secs(30);

/// Why a request came to nothing.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub(crate) enum Failure {
    /// No connection could be made to `host`: refused, unresolvable, or
    /// timed out while connecting. The one failure that is a fact about the
    /// host from this machine, which is why it ends a load at once.
    #[error("could not connect to {host}")]
    Unreachable { host: String },
    /// "Too many requests": the host asking for fewer readers.
    #[error("rate limited")]
    RateLimited { after: Option<Duration> },
    /// Any other answer than the one asked for.
    #[error("HTTP {0}")]
    Status(u16),
    #[error("{0}")]
    Other(String),
}

/// What the probe learned about a stream.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Probe {
    /// Where the redirects ended. Addon URLs are signing endpoints that
    /// redirect to a CDN, and walking that chain again for every range
    /// request is a round trip each time through the slowest hop in it.
    pub(crate) resolved: String,
    /// The file's length, when the host said.
    pub(crate) size: Option<u64>,
    /// The host answered a range with 206 and a `Content-Range` total.
    pub(crate) ranged: bool,
}

/// A ranged response body, read as it arrives.
pub(crate) type Body = Box<dyn Read + Send>;

/// Requests for one stream, with its headers.
#[derive(Clone)]
pub(crate) struct Client {
    agent: ureq::Agent,
    headers: Vec<(String, String)>,
}

impl std::fmt::Debug for Client {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Client").finish_non_exhaustive()
    }
}

impl Client {
    /// A client sending `headers` (and `user_agent`, when given) with every
    /// request, keeping up to `connections` connections open for reuse.
    pub(crate) fn new(
        headers: &[(String, String)],
        user_agent: Option<&str>,
        connections: usize,
    ) -> Self {
        let mut config = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .timeout_connect(Some(CONNECT_TIMEOUT))
            .timeout_recv_response(Some(RESPONSE_TIMEOUT))
            .max_idle_connections_per_host(connections + 2)
            .tls_config(
                TlsConfig::builder()
                    .root_certs(RootCerts::PlatformVerifier)
                    .build(),
            );
        if let Some(agent) = user_agent {
            config = config.user_agent(agent);
        }
        Self {
            agent: config.build().into(),
            headers: headers.to_vec(),
        }
    }

    /// Asks the host, with a one-byte range, whether it serves ranges and
    /// how long the file is -- both answers in one request.
    pub(crate) fn probe(&self, url: &str) -> Result<Probe, Failure> {
        let response = self.get(url, "bytes=0-0", None)?;
        let status = response.status();
        if let Some(limited) = rate_limited(&response) {
            return Err(limited);
        }
        let total = response
            .headers()
            .get("content-range")
            .and_then(|v| v.to_str().ok())
            .and_then(total_from_content_range);
        let resolved = response.get_uri().to_string();
        if !status.is_success() {
            return Err(Failure::Status(status.as_u16()));
        }
        Ok(Probe {
            resolved,
            size: total,
            ranged: status == StatusCode::PARTIAL_CONTENT && total.is_some(),
        })
    }

    /// Bytes `start..=end` of `url`, as they arrive.
    pub(crate) fn range(&self, url: &str, start: u64, end: u64) -> Result<Body, Failure> {
        let bytes = end - start + 1;
        let budget = RESPONSE_TIMEOUT + Duration::from_secs(bytes / SLOWEST_BYTES_PER_S);
        let response = self.get(url, &format!("bytes={start}-{end}"), Some(budget))?;
        if let Some(limited) = rate_limited(&response) {
            return Err(limited);
        }
        let status = response.status();
        // 200 answers a range with the whole file from byte 0, which is the
        // right bytes only if byte 0 was asked for.
        if status != StatusCode::PARTIAL_CONTENT && !(status == StatusCode::OK && start == 0) {
            return Err(Failure::Status(status.as_u16()));
        }
        Ok(Box::new(response.into_body().into_reader()))
    }

    fn get(
        &self,
        url: &str,
        range: &str,
        body_budget: Option<Duration>,
    ) -> Result<ureq::http::Response<ureq::Body>, Failure> {
        let mut request = self.agent.get(url).header("Range", range);
        for (name, value) in &self.headers {
            request = request.header(name, value);
        }
        let result = if body_budget.is_some() {
            request
                .config()
                .timeout_recv_body(body_budget)
                .build()
                .call()
        } else {
            request.call()
        };
        result.map_err(|error| classify(&error, url))
    }
}

/// The failure a `ureq` error is, for `url`.
fn classify(error: &ureq::Error, url: &str) -> Failure {
    let unreachable = match error {
        ureq::Error::HostNotFound | ureq::Error::ConnectionFailed => true,
        ureq::Error::Timeout(timeout) => {
            matches!(timeout, ureq::Timeout::Connect | ureq::Timeout::Resolve)
        }
        ureq::Error::Io(io) => matches!(
            io.kind(),
            std::io::ErrorKind::ConnectionRefused | std::io::ErrorKind::HostUnreachable
        ),
        _ => false,
    };
    if unreachable {
        Failure::Unreachable {
            host: host_of(url).unwrap_or_else(|| "?".to_owned()),
        }
    } else {
        Failure::Other(error.to_string())
    }
}

/// A rate-limit answer: 429, or 503 with a `Retry-After` (a plain 503 is an
/// outage, not a limit).
fn rate_limited(response: &ureq::http::Response<ureq::Body>) -> Option<Failure> {
    let status = response.status();
    let after = response
        .headers()
        .get("retry-after")
        .and_then(|v| v.to_str().ok())
        .and_then(retry_after);
    let limited = status == StatusCode::TOO_MANY_REQUESTS
        || (status == StatusCode::SERVICE_UNAVAILABLE && after.is_some());
    limited.then_some(Failure::RateLimited { after })
}

/// `Retry-After` in seconds, capped. Only the delta-seconds form: an
/// HTTP-date needs a clock this machine cannot trust to agree with the host's.
fn retry_after(value: &str) -> Option<Duration> {
    let seconds: u64 = value.trim().parse().ok()?;
    Some(Duration::from_secs(seconds).min(MAX_RETRY_AFTER))
}

/// The total in `bytes 0-0/12345`, or None for `*` or a malformed value.
pub(crate) fn total_from_content_range(value: &str) -> Option<u64> {
    let (_, total) = value.trim().rsplit_once('/')?;
    total.trim().parse().ok()
}

/// The host of `url`, for logs and failure messages.
pub(crate) fn host_of(url: &str) -> Option<String> {
    url.parse::<Uri>()
        .ok()
        .and_then(|uri| uri.host().map(str::to_owned))
}

/// What a resolver answers when it refuses to answer: a blocklist sinkhole.
/// The connection that follows is refused at once, which is byte for byte
/// what a dead host looks like -- and wants the opposite response.
const SINKHOLES: [&str; 3] = ["0.0.0.0", "::", "::1"];

/// One line a viewer can act on, saying why `host` took no connection: a
/// host the machine's own DNS has blocked, one that does not resolve, or
/// one that refused.
pub(crate) fn describe_unreachable(host: &str) -> String {
    match (host, 443).to_socket_addrs() {
        Err(_) => format!("{host} does not resolve on this network."),
        Ok(addresses) => {
            let addresses: Vec<String> = addresses.map(|a| a.ip().to_string()).collect();
            if !addresses.is_empty() && addresses.iter().all(|a| SINKHOLES.contains(&a.as_str())) {
                format!("{host} is blocked by this machine's DNS (it resolves to 0.0.0.0).")
            } else {
                format!("{host} refused the connection.")
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_total_comes_from_the_content_range() {
        assert_eq!(
            total_from_content_range("bytes 0-0/1335015425"),
            Some(1_335_015_425)
        );
        assert_eq!(total_from_content_range("bytes 0-0/*"), None);
        assert_eq!(total_from_content_range("garbage"), None);
    }

    #[test]
    fn retry_after_is_seconds_and_capped() {
        assert_eq!(retry_after("3"), Some(Duration::from_secs(3)));
        assert_eq!(retry_after("600"), Some(MAX_RETRY_AFTER));
        assert_eq!(retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), None);
    }

    #[test]
    fn hosts_are_read_off_urls() {
        assert_eq!(
            host_of("https://cdn.example.com/a/b.mkv?token=1").as_deref(),
            Some("cdn.example.com")
        );
        assert_eq!(host_of("not a url"), None);
    }

    #[test]
    fn a_refused_connection_names_the_host() {
        // Nothing listens on port 1 of loopback.
        let client = Client::new(&[], None, 1);
        assert_eq!(
            client.probe("http://127.0.0.1:1/file.mkv"),
            Err(Failure::Unreachable {
                host: "127.0.0.1".to_owned()
            })
        );
    }
}
