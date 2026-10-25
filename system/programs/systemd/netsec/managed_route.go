package route

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"strings"

	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/config"
)

// Maps replace duplicate objects, whereas struct pointers can merge them.
// Reject duplicates before interpreting any field with the upstream schema.
func lfPatchValue(decoder *json.Decoder, depth int) (any, error) {
	if depth > 64 {
		return nil, fmt.Errorf("configuration nesting limit")
	}
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	delimiter, nested := token.(json.Delim)
	if !nested {
		return token, nil
	}
	switch delimiter {
	case '{':
		object := map[string]any{}
		for decoder.More() {
			member, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			key, ok := member.(string)
			if !ok {
				return nil, fmt.Errorf("invalid member")
			}
			if _, exists := object[key]; exists {
				return nil, fmt.Errorf("duplicate member")
			}
			value, err := lfPatchValue(decoder, depth+1)
			if err != nil {
				return nil, err
			}
			object[key] = value
		}
		_, err = decoder.Token()
		return object, err
	case '[':
		array := []any{}
		for decoder.More() {
			value, err := lfPatchValue(decoder, depth+1)
			if err != nil {
				return nil, err
			}
			array = append(array, value)
		}
		_, err = decoder.Token()
		return array, err
	}
	return nil, fmt.Errorf("unexpected delimiter")
}

// Use canonical schema names, without interpreting keys inside user maps
// (for example TUIC usernames) as configuration fields.
func lfPatchKeys(object map[string]any, schema reflect.Type) error {
	for schema.Kind() == reflect.Pointer {
		schema = schema.Elem()
	}
	if schema.Kind() != reflect.Struct {
		return nil
	}
	for key, value := range object {
		for index := 0; index < schema.NumField(); index++ {
			field := schema.Field(index)
			name := strings.Split(field.Tag.Get("json"), ",")[0]
			if name == "" {
				name = field.Name
			}
			if !strings.EqualFold(key, name) {
				continue
			}
			if key != name {
				return fmt.Errorf("noncanonical member")
			}
			if child, ok := value.(map[string]any); ok {
				if err := lfPatchKeys(child, field.Type); err != nil {
					return err
				}
			}
			break
		}
	}
	return nil
}

func lfPatchPruneNulls(object map[string]any) {
	for key, value := range object {
		if value == nil {
			delete(object, key)
		} else if child, ok := value.(map[string]any); ok {
			lfPatchPruneNulls(child)
		}
	}
}

func lfDecodeManagedPatch(w http.ResponseWriter, r *http.Request) (*configSchema, bool) {
	reject := func() (*configSchema, bool) {
		http.Error(w, "managed proxy policy rejects this change", http.StatusBadRequest)
		return nil, false
	}
	data, err := io.ReadAll(io.LimitReader(r.Body, 4*1024*1024+1))
	if err != nil || len(data) > 4*1024*1024 {
		return reject()
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	value, err := lfPatchValue(decoder, 0)
	document, object := value.(map[string]any)
	if err != nil || !object {
		return reject()
	}
	if _, err = decoder.Token(); err != io.EOF {
		return reject()
	}
	if err = lfPatchKeys(document, reflect.TypeOf(configSchema{})); err != nil {
		return reject()
	}
	general := &configSchema{}
	if err = json.Unmarshal(data, general); err != nil {
		return reject()
	}
	// Null pointers mean unchanged. Non-pointer bools in supplied nested structs
	// retain upstream defaults, including tun.enable=false when omitted.
	canonical, err := json.Marshal(general)
	if err != nil {
		return reject()
	}
	var applied map[string]any
	if err = json.Unmarshal(canonical, &applied); err != nil {
		return reject()
	}
	lfPatchPruneNulls(document)
	lfPatchPruneNulls(applied)
	// Also reject protected profile-only fields that PATCH would otherwise ignore.
	if config.ValidateManagedPatch(document) != nil || config.ValidateManagedPatch(applied) != nil {
		return reject()
	}
	// The handler consumes this very schema; never re-decode the original body.
	return general, true
}
