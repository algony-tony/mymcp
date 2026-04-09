#!/usr/bin/env bats
# Tests for deploy/install_lib.sh helper functions.
# Run: bats tests/test_install.bats

setup() {
    # Source the library under test
    export AUTO_YES=false
    source "$BATS_TEST_DIRNAME/../deploy/install_lib.sh"
}

# =========================================================================
# confirm() tests
# =========================================================================

@test "confirm: AUTO_YES=true with default Y returns 0" {
    AUTO_YES=true
    run confirm "Install?" Y
    [ "$status" -eq 0 ]
}

@test "confirm: AUTO_YES=true with default N returns 1" {
    AUTO_YES=true
    run confirm "Install?" N
    [ "$status" -eq 1 ]
}

@test "confirm: AUTO_YES=true with no default arg returns 0 (default is Y)" {
    AUTO_YES=true
    run confirm "Install?"
    [ "$status" -eq 0 ]
}

@test "confirm: interactive default=Y, user types enter → accept" {
    AUTO_YES=false
    run bash -c 'source deploy/install_lib.sh; AUTO_YES=false; echo "" | confirm "Q?" Y && echo yes || echo no'
    [[ "$output" == *"yes"* ]]
}

@test "confirm: interactive default=Y, user types n → reject" {
    AUTO_YES=false
    run bash -c 'source deploy/install_lib.sh; AUTO_YES=false; echo "n" | confirm "Q?" Y && echo yes || echo no'
    [[ "$output" == *"no"* ]]
}

@test "confirm: interactive default=N, user types enter → reject" {
    AUTO_YES=false
    run bash -c 'source deploy/install_lib.sh; AUTO_YES=false; echo "" | confirm "Q?" N && echo yes || echo no'
    [[ "$output" == *"no"* ]]
}

@test "confirm: interactive default=N, user types y → accept" {
    AUTO_YES=false
    run bash -c 'source deploy/install_lib.sh; AUTO_YES=false; echo "y" | confirm "Q?" N && echo yes || echo no'
    [[ "$output" == *"yes"* ]]
}

# =========================================================================
# prompt_value() tests
# =========================================================================

@test "prompt_value: AUTO_YES=true returns default" {
    AUTO_YES=true
    run prompt_value "Port" "8765"
    [ "$output" = "8765" ]
}

@test "prompt_value: interactive, user enters value" {
    run bash -c 'source deploy/install_lib.sh; AUTO_YES=false; echo "9000" | prompt_value "Port" "8765"'
    [[ "$output" == *"9000"* ]]
}

@test "prompt_value: interactive, user presses enter → default" {
    run bash -c 'source deploy/install_lib.sh; AUTO_YES=false; echo "" | prompt_value "Port" "8765"'
    [[ "$output" == *"8765"* ]]
}

# =========================================================================
# validate_app_dir() tests
# =========================================================================

@test "validate_app_dir: absolute path succeeds" {
    run validate_app_dir "/opt/mymcp"
    [ "$status" -eq 0 ]
}

@test "validate_app_dir: relative path fails" {
    run validate_app_dir "opt/mymcp"
    [ "$status" -eq 1 ]
    [[ "$output" == *"must be absolute"* ]]
}

@test "validate_app_dir: empty string fails" {
    run validate_app_dir ""
    [ "$status" -eq 1 ]
}

@test "validate_app_dir: dot path fails" {
    run validate_app_dir "./mymcp"
    [ "$status" -eq 1 ]
}

# =========================================================================
# find_python() tests
# =========================================================================

@test "find_python: finds a python with low min_minor" {
    run find_python 3
    [ "$status" -eq 0 ]
    [[ "$output" == python* ]]
}

@test "find_python: found python version meets requested minimum" {
    local py
    py=$(find_python 3)
    ver=$("$py" -c 'import sys; print(sys.version_info.minor)')
    [ "$ver" -ge 3 ]
}

@test "find_python: impossible min_minor returns failure" {
    run find_python 99
    [ "$status" -eq 1 ]
}

@test "find_python: returns failure when no python meets threshold" {
    # min_minor=99 is impossible on any real system
    run find_python 99
    [ "$status" -eq 1 ]
    [ -z "$output" ]
}

# =========================================================================
# install.sh argument parsing
# =========================================================================

@test "install.sh -h prints usage and exits 0" {
    run bash deploy/install.sh -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage"* ]]
    [[ "$output" == *"-y"* ]]
}

@test "install.sh --invalid-flag exits non-zero" {
    run bash deploy/install.sh -z
    [ "$status" -ne 0 ]
}

# =========================================================================
# sh compatibility: re-exec guard
# =========================================================================

@test "install.sh -h works even when invoked via sh" {
    run sh deploy/install.sh -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage"* ]]
}
