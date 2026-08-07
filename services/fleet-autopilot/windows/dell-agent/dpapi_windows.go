//go:build windows

package main

import (
	"errors"
	"syscall"
	"unsafe"
)

type dataBlob struct {
	cbData uint32
	pbData *byte
}

var (
	crypt32              = syscall.NewLazyDLL("crypt32.dll")
	kernel32             = syscall.NewLazyDLL("kernel32.dll")
	procCryptProtectData = crypt32.NewProc("CryptProtectData")
	procCryptUnprotect   = crypt32.NewProc("CryptUnprotectData")
	procLocalFree        = kernel32.NewProc("LocalFree")
)

const cryptProtectLocalMachine = 0x4

func blob(value []byte) dataBlob {
	if len(value) == 0 {
		return dataBlob{}
	}
	return dataBlob{cbData: uint32(len(value)), pbData: &value[0]}
}

func copyBlob(value dataBlob) []byte {
	if value.cbData == 0 || value.pbData == nil {
		return nil
	}
	return append([]byte(nil), unsafe.Slice(value.pbData, int(value.cbData))...)
}

func protectPrivateKey(value []byte) ([]byte, error) {
	in := blob(value)
	var out dataBlob
	ok, _, callErr := procCryptProtectData.Call(
		uintptr(unsafe.Pointer(&in)), 0, 0, 0, 0,
		uintptr(cryptProtectLocalMachine), uintptr(unsafe.Pointer(&out)),
	)
	if ok == 0 {
		return nil, callErr
	}
	defer procLocalFree.Call(uintptr(unsafe.Pointer(out.pbData)))
	return copyBlob(out), nil
}

func unprotectPrivateKey(value []byte) ([]byte, error) {
	in := blob(value)
	var out dataBlob
	ok, _, callErr := procCryptUnprotect.Call(
		uintptr(unsafe.Pointer(&in)), 0, 0, 0, 0, 0, uintptr(unsafe.Pointer(&out)),
	)
	if ok == 0 {
		return nil, callErr
	}
	defer procLocalFree.Call(uintptr(unsafe.Pointer(out.pbData)))
	plain := copyBlob(out)
	if len(plain) == 0 {
		return nil, errors.New("DPAPI returned an empty identity")
	}
	return plain, nil
}
