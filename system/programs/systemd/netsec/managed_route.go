package route

import (
	"bytes"
	"encoding/json"
	"fmt"
	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/config"
	"io"
)

func lfValidateManagedPatch(w http.ResponseWriter, r *http.Request) bool {
	data, err := io.ReadAll(io.LimitReader(r.Body, 4*1024*1024+1))
	if err != nil || len(data) > 4*1024*1024 {
		http.Error(w, "invalid managed configuration", http.StatusBadRequest)
		return false
	}
	var document map[string]any
	if err = json.Unmarshal(data, &document); err == nil {
		if document == nil {
			err = fmt.Errorf("configuration must be an object")
		} else {
			err = config.ValidateManagedPatch(document)
		}
	}
	if err != nil {
		http.Error(w, "managed proxy policy rejects this change", http.StatusBadRequest)
		return false
	}
	r.Body = io.NopCloser(bytes.NewReader(data))
	return true
}
