package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestBaseURL(t *testing.T) {
	for in, want := range map[string]string{
		"http://h:8180":          "http://h:8180/api/v1",
		"http://h:8180/":         "http://h:8180/api/v1",
		"http://h:8180/api/v1":   "http://h:8180/api/v1",
		" http://h:8180/api/v1/": "http://h:8180/api/v1",
	} {
		if got := BaseURL(in); got != want {
			t.Errorf("BaseURL(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestLoginCapturesSessionCookieAndUsesBearer(t *testing.T) {
	var sawBearer string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/auth/login":
			var body map[string]string
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body["username"] != "admin" || body["password"] != "pw" {
				w.WriteHeader(401)
				_, _ = w.Write([]byte(`{"success":false,"error":{"code":"INVALID_CREDENTIALS","message":"no"}}`))
				return
			}
			http.SetCookie(w, &http.Cookie{Name: "other_session", Value: "fallback"})
			http.SetCookie(w, &http.Cookie{Name: "gpudeck_session", Value: "tok"})
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"success":true,"data":{},"error":null,"meta":null}`))
		case "/api/v1/auth/me":
			sawBearer = r.Header.Get("Authorization")
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"success":true,"data":{"username":"admin","extra":1},"error":null,"meta":null}`))
		default:
			w.WriteHeader(404)
		}
	}))
	defer srv.Close()

	api, err := New(context.Background(), Config{Endpoint: srv.URL, Username: "admin", Password: "pw"})
	if err != nil {
		t.Fatal(err)
	}
	obj, err := DecodeObject(Raw(api.GetMe(context.Background())))
	if err != nil {
		t.Fatal(err)
	}
	if obj["username"] != "admin" || sawBearer != "Bearer tok" {
		t.Fatalf("got %v, bearer %q", obj, sawBearer)
	}

	_, err = New(context.Background(), Config{Endpoint: srv.URL, Username: "admin", Password: "bad"})
	if err == nil {
		t.Fatal("expected login failure")
	}
}

func TestCheckAndDecode(t *testing.T) {
	err := Check(404, []byte(`{"success":false,"error":{"code":"RESOURCE_NOT_FOUND","message":"gone"}}`), 200)
	if !IsNotFound(err) {
		t.Fatalf("expected not found, got %v", err)
	}
	if err.Error() != "gpudeck api: RESOURCE_NOT_FOUND (404): gone" {
		t.Fatalf("message: %v", err)
	}
	err = Check(502, []byte("<html>bad gateway</html>"), 200)
	if ae, ok := err.(*APIError); !ok || ae.Message != "<html>bad gateway</html>" {
		t.Fatalf("raw body error: %#v", err)
	}
	if _, err := Decode[map[string]any](200, []byte(`{"success":false,"error":{"code":"X","message":"m"}}`)); err == nil {
		t.Fatal("success=false must be an error")
	}
	page, err := DecodeList(200, []byte(`{"success":true,"data":[{"id":"a"}],"meta":{"has_more":true}}`), nil)
	if err != nil || len(page.Items) != 1 || !page.HasMore {
		t.Fatalf("list: %v %v", page, err)
	}
}

func TestNewValidatesConfig(t *testing.T) {
	if _, err := New(context.Background(), Config{}); err == nil {
		t.Fatal("endpoint required")
	}
	if _, err := New(context.Background(), Config{Endpoint: "http://x"}); err == nil {
		t.Fatal("credentials required")
	}
	api, err := New(context.Background(), Config{Endpoint: "http://x", Token: "t"})
	if err != nil || api.BaseURL != "http://x/api/v1" {
		t.Fatalf("token config: %v %v", api, err)
	}
}
