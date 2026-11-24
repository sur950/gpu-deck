package client

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const (
	apiPrefix      = "/api/v1"
	sessionCookie  = "gpudeck_session"
	userAgent      = "terraform-provider-gpudeck"
	defaultTimeout = 60 * time.Second
)

// Config is what the provider block / environment supplies.
type Config struct {
	Endpoint           string
	Username           string
	Password           string
	Token              string
	InsecureSkipVerify bool
	Timeout            time.Duration
}

// API wraps the generated client with a session token and envelope helpers.
type API struct {
	*ClientWithResponses
	BaseURL string
	token   string
}

// APIError is an error envelope ({success:false, error:{code,message}}) or an unexpected status.
type APIError struct {
	Status  int
	Code    string
	Message string
	Details json.RawMessage
}

func (e *APIError) Error() string {
	if e.Code != "" {
		return fmt.Sprintf("gpudeck api: %s (%d): %s", e.Code, e.Status, e.Message)
	}
	return fmt.Sprintf("gpudeck api: HTTP %d: %s", e.Status, e.Message)
}

// IsNotFound reports whether err is a 404 / RESOURCE_NOT_FOUND response.
func IsNotFound(err error) bool {
	var ae *APIError
	if errors.As(err, &ae) {
		return ae.Status == http.StatusNotFound || ae.Code == "RESOURCE_NOT_FOUND"
	}
	return false
}

// Envelope is the admin API response envelope with a typed data member.
type Envelope[T any] struct {
	Success bool            `json:"success"`
	Data    T               `json:"data"`
	Error   *ErrorBody      `json:"error"`
	Meta    json.RawMessage `json:"meta"`
}

// BaseURL normalises an endpoint to the admin API base (…/api/v1).
func BaseURL(endpoint string) string {
	ep := strings.TrimRight(strings.TrimSpace(endpoint), "/")
	if strings.HasSuffix(ep, apiPrefix) {
		return ep
	}
	return ep + apiPrefix
}

func httpClient(cfg Config) *http.Client {
	timeout := cfg.Timeout
	if timeout == 0 {
		timeout = defaultTimeout
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if cfg.InsecureSkipVerify {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec // opt-in
	}
	return &http.Client{Timeout: timeout, Transport: transport}
}

// New builds an authenticated API. With Username/Password it logs in and captures the session
// token from the Set-Cookie header; with Token it uses the token directly as Bearer.
func New(ctx context.Context, cfg Config) (*API, error) {
	if cfg.Endpoint == "" {
		return nil, errors.New("endpoint is required")
	}
	base := BaseURL(cfg.Endpoint)
	hc := httpClient(cfg)
	token := cfg.Token
	if token == "" {
		if cfg.Username == "" || cfg.Password == "" {
			return nil, errors.New("either token or username and password are required")
		}
		t, err := Login(ctx, base, hc, cfg.Username, cfg.Password)
		if err != nil {
			return nil, err
		}
		token = t
	}
	authed, err := NewClientWithResponses(base,
		WithHTTPClient(hc),
		WithRequestEditorFn(func(_ context.Context, req *http.Request) error {
			req.Header.Set("Authorization", "Bearer "+token)
			req.Header.Set("User-Agent", userAgent)
			req.Header.Set("Accept", "application/json")
			return nil
		}),
	)
	if err != nil {
		return nil, err
	}
	return &API{ClientWithResponses: authed, BaseURL: base, token: token}, nil
}

// Login posts to /auth/login and returns the session token found in the gpudeck_session cookie
// (fallback: any cookie whose name ends in "_session").
func Login(ctx context.Context, base string, hc *http.Client, username, password string) (string, error) {
	anon, err := NewClientWithResponses(base, WithHTTPClient(hc),
		WithRequestEditorFn(func(_ context.Context, req *http.Request) error {
			req.Header.Set("User-Agent", userAgent)
			return nil
		}))
	if err != nil {
		return "", err
	}
	resp, err := anon.LoginWithResponse(ctx, LoginJSONRequestBody{Username: username, Password: password})
	if err != nil {
		return "", fmt.Errorf("login request to %s failed: %w", base, err)
	}
	if err := Check(resp.StatusCode(), resp.Body, http.StatusOK); err != nil {
		return "", fmt.Errorf("login failed: %w", err)
	}
	var fallback string
	for _, c := range resp.HTTPResponse.Cookies() {
		if c.Name == sessionCookie {
			return c.Value, nil
		}
		if strings.HasSuffix(c.Name, "_session") && fallback == "" {
			fallback = c.Value
		}
	}
	if fallback != "" {
		return fallback, nil
	}
	return "", errors.New("login succeeded but no *_session cookie was returned")
}

// Check returns an *APIError unless status is one of want.
func Check(status int, body []byte, want ...int) error {
	for _, w := range want {
		if status == w {
			return nil
		}
	}
	ae := &APIError{Status: status, Message: http.StatusText(status)}
	var env Envelope[json.RawMessage]
	if len(body) > 0 && json.Unmarshal(body, &env) == nil && env.Error != nil {
		ae.Code = env.Error.Code
		ae.Message = env.Error.Message
		if env.Error.Details != nil {
			if raw, err := json.Marshal(env.Error.Details); err == nil {
				ae.Details = raw
			}
		}
	} else if len(body) > 0 {
		msg := strings.TrimSpace(string(body))
		if len(msg) > 300 {
			msg = msg[:300]
		}
		ae.Message = msg
	}
	return ae
}

// Decode unwraps an envelope body into T after checking the status.
func Decode[T any](status int, body []byte, want ...int) (T, error) {
	var zero T
	if len(want) == 0 {
		want = []int{http.StatusOK, http.StatusCreated, http.StatusAccepted}
	}
	if err := Check(status, body, want...); err != nil {
		return zero, err
	}
	var env Envelope[T]
	if err := json.Unmarshal(body, &env); err != nil {
		return zero, fmt.Errorf("decode envelope: %w", err)
	}
	if !env.Success {
		if env.Error != nil {
			return zero, &APIError{Status: status, Code: env.Error.Code, Message: env.Error.Message}
		}
		return zero, &APIError{Status: status, Message: "success=false without error body"}
	}
	return env.Data, nil
}

// Raw reads a response produced by a generated ClientInterface method (e.g.
// api.CreateClusterWithBody) and returns its status and body. Resources use the generated
// request builders for paths/parameters but decode envelopes themselves, so an extra or
// differently typed response field never fails a Terraform run.
func Raw(resp *http.Response, err error) (int, []byte, error) {
	if err != nil {
		return 0, nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return resp.StatusCode, nil, fmt.Errorf("read response: %w", err)
	}
	return resp.StatusCode, body, nil
}

// DecodeObject unwraps an envelope whose data is a JSON object.
func DecodeObject(status int, body []byte, err error, want ...int) (map[string]any, error) {
	if err != nil {
		return nil, err
	}
	return Decode[map[string]any](status, body, want...)
}

// ListPage is one page of a list envelope.
type ListPage struct {
	Items   []map[string]any
	HasMore bool
}

// DecodeList unwraps a list envelope ({data: [...], meta: {has_more}}).
func DecodeList(status int, body []byte, err error) (ListPage, error) {
	if err != nil {
		return ListPage{}, err
	}
	if err := Check(status, body, http.StatusOK); err != nil {
		return ListPage{}, err
	}
	var env struct {
		Success bool             `json:"success"`
		Data    []map[string]any `json:"data"`
		Meta    *struct {
			HasMore bool `json:"has_more"`
		} `json:"meta"`
	}
	if err := json.Unmarshal(body, &env); err != nil {
		return ListPage{}, fmt.Errorf("decode list envelope: %w", err)
	}
	page := ListPage{Items: env.Data}
	if env.Meta != nil {
		page.HasMore = env.Meta.HasMore
	}
	return page, nil
}

// JSONBody encodes a request body for the generated ...WithBody methods.
func JSONBody(v any) (io.Reader, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	return bytes.NewReader(raw), nil
}

// ContentJSON is the request content type for every admin API body.
const ContentJSON = "application/json"
