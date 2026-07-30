package main

import (
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

func main() {
	body := strings.NewReader(`{"Q":"repo:__edsys_healthcheck_no_match__","Opts":{"MaxDocDisplayCount":1}}`)
	request, err := http.NewRequest(http.MethodPost, "http://127.0.0.1:6070/api/search", body)
	if err != nil {
		os.Exit(1)
	}
	request.Header.Set("Content-Type", "application/json")
	client := http.Client{Timeout: 3 * time.Second}
	response, err := client.Do(request)
	if err != nil {
		os.Exit(1)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusOK {
		os.Exit(1)
	}
}
