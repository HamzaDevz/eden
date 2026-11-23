// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// SPDX-FileCopyrightText: Copyright 2018 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <cstring>
#include <openssl/err.h>
#include <openssl/evp.h>
#include "common/assert.h"
#include "common/logging.h"
#include "core/crypto/aes_util.h"
#include "core/crypto/key_manager.h"

// The bundled OpenSSL is built without assembly, so its AES runs as table C code (~350 MB/s on the Odin 3).
// Game data reads go through AES-128-CTR, so it is done here with the ARMv8 crypto extension instead (~4 GB/s).
#if defined(__aarch64__) && defined(__linux__) && defined(__clang__)
#define EDEN_AES_ARMV8_CTR
#include <arm_neon.h>
#include <asm/hwcap.h>
#include <sys/auxv.h>
#endif

namespace Core::Crypto {
namespace {
using NintendoTweak = std::array<u8, 16>;
constexpr std::size_t AesBlockBytes = 16;

#ifdef EDEN_AES_ARMV8_CTR
using RoundKeys = std::array<std::array<u8, 16>, 11>;

// AESE with a zero round key is ShiftRows(SubBytes(x)); with the same word in every column ShiftRows is a no-op
__attribute__((target("aes"))) u32 SubWord(u32 w) {
    const uint8x16_t s = vaeseq_u8(vreinterpretq_u8_u32(vdupq_n_u32(w)), vdupq_n_u8(0));
    return vgetq_lane_u32(vreinterpretq_u32_u8(s), 0);
}

__attribute__((target("aes"))) RoundKeys ExpandKey128(const u8* key) {
    std::array<u32, 44> w;
    std::memcpy(w.data(), key, 16);
    u8 rcon = 1;
    for (std::size_t i = 4; i < w.size(); ++i) {
        u32 t = w[i - 1];
        if (i % 4 == 0) {
            t = SubWord(t);
            t = ((t >> 8) | (t << 24)) ^ rcon;
            rcon = static_cast<u8>((rcon << 1) ^ ((rcon & 0x80) ? 0x1b : 0));
        }
        w[i] = w[i - 4] ^ t;
    }
    RoundKeys out;
    std::memcpy(out.data(), w.data(), sizeof(w));
    return out;
}

__attribute__((target("aes"))) inline uint8x16_t EncryptBlock(const uint8x16_t* rk, uint8x16_t s) {
    for (std::size_t r = 0; r < 9; ++r) {
        s = vaesmcq_u8(vaeseq_u8(s, rk[r]));
    }
    return veorq_u8(vaeseq_u8(s, rk[9]), rk[10]);
}

// Same output as OpenSSL's AES-128-CTR: 128-bit big-endian counter, a trailing partial block consumes a whole one
__attribute__((target("aes"))) void Armv8Ctr(const RoundKeys& keys, std::array<u8, 16>& counter, const u8* src,
                                             std::size_t size, u8* dest) {
    uint8x16_t rk[11];
    for (std::size_t i = 0; i < 11; ++i) {
        rk[i] = vld1q_u8(keys[i].data());
    }
    u64 hi, lo;
    std::memcpy(&hi, counter.data(), 8);
    std::memcpy(&lo, counter.data() + 8, 8);
    hi = __builtin_bswap64(hi);
    lo = __builtin_bswap64(lo);
    const auto next = [&] {
        const u64 be[2] = {__builtin_bswap64(hi), __builtin_bswap64(lo)};
        if (++lo == 0) {
            ++hi;
        }
        return vld1q_u8(reinterpret_cast<const u8*>(be));
    };
    std::size_t off = 0;
    for (; off + 64 <= size; off += 64) {
        uint8x16_t s0 = next(), s1 = next(), s2 = next(), s3 = next();
        for (std::size_t r = 0; r < 9; ++r) {
            s0 = vaesmcq_u8(vaeseq_u8(s0, rk[r]));
            s1 = vaesmcq_u8(vaeseq_u8(s1, rk[r]));
            s2 = vaesmcq_u8(vaeseq_u8(s2, rk[r]));
            s3 = vaesmcq_u8(vaeseq_u8(s3, rk[r]));
        }
        s0 = veorq_u8(vaeseq_u8(s0, rk[9]), rk[10]);
        s1 = veorq_u8(vaeseq_u8(s1, rk[9]), rk[10]);
        s2 = veorq_u8(vaeseq_u8(s2, rk[9]), rk[10]);
        s3 = veorq_u8(vaeseq_u8(s3, rk[9]), rk[10]);
        vst1q_u8(dest + off, veorq_u8(vld1q_u8(src + off), s0));
        vst1q_u8(dest + off + 16, veorq_u8(vld1q_u8(src + off + 16), s1));
        vst1q_u8(dest + off + 32, veorq_u8(vld1q_u8(src + off + 32), s2));
        vst1q_u8(dest + off + 48, veorq_u8(vld1q_u8(src + off + 48), s3));
    }
    for (; off < size; off += AesBlockBytes) {
        std::array<u8, AesBlockBytes> ks;
        vst1q_u8(ks.data(), EncryptBlock(rk, next()));
        const std::size_t n = std::min(AesBlockBytes, size - off);
        for (std::size_t i = 0; i < n; ++i) {
            dest[off + i] = src[off + i] ^ ks[i];
        }
    }
    const u64 be[2] = {__builtin_bswap64(hi), __builtin_bswap64(lo)};
    std::memcpy(counter.data(), be, sizeof(be));
}
#endif

NintendoTweak CalculateNintendoTweak(std::size_t sector_id) {
    NintendoTweak out{};
    for (std::size_t i = 0xF; i <= 0xF; --i) {
        out[i] = sector_id & 0xFF;
        sector_id >>= 8;
    }
    return out;
}
} // Anonymous namespace

// Structure to hide OpenSSL types from header file
struct CipherContext {
    EVP_CIPHER_CTX* encryption_context = nullptr;
    EVP_CIPHER_CTX* decryption_context = nullptr;
    EVP_CIPHER* cipher = nullptr;
#ifdef EDEN_AES_ARMV8_CTR
    bool armv8_ctr = false;
    RoundKeys round_keys{};
    // OpenSSL keeps a running counter per context across Transcode calls; mirror that
    std::array<u8, 16> encryption_counter{};
    std::array<u8, 16> decryption_counter{};
#endif
};

static inline const std::string GetCipherName(Mode mode, u32 key_size) {
    std::string cipher;
    std::size_t effective_bits = key_size * 8;
    switch (mode) {
    case Mode::CTR:
        cipher = "CTR";
        break;
    case Mode::ECB:
        cipher = "ECB";
        break;
    case Mode::XTS:
        cipher = "XTS";
        effective_bits /= 2;
        break;
    default:
        UNREACHABLE();
    }
    return std::format("AES-{}-{}", effective_bits, cipher);
};

static EVP_CIPHER *GetCipher(Mode mode, u32 key_size) {
    static auto const fetch_cipher = [](Mode m, u32 k) {
        return EVP_CIPHER_fetch(nullptr, GetCipherName(m, k).c_str(), nullptr);
    };
    static const struct {
        EVP_CIPHER* ctr_16 = fetch_cipher(Mode::CTR, 16);
        EVP_CIPHER* ecb_16 = fetch_cipher(Mode::ECB, 16);
        EVP_CIPHER* xts_16 = fetch_cipher(Mode::XTS, 16);
        EVP_CIPHER* ctr_32 = fetch_cipher(Mode::CTR, 32);
        EVP_CIPHER* ecb_32 = fetch_cipher(Mode::ECB, 32);
        EVP_CIPHER* xts_32 = fetch_cipher(Mode::XTS, 32);
    } ciphers = {};
    switch (mode) {
    case Mode::CTR:
        return key_size == 16 ? ciphers.ctr_16 : ciphers.ctr_32;
    case Mode::ECB:
        return key_size == 16 ? ciphers.ecb_16 : ciphers.ecb_32;
    case Mode::XTS:
        return key_size == 16 ? ciphers.xts_16 : ciphers.xts_32;
    default:
        UNIMPLEMENTED();
    }
    return nullptr;
}

// TODO: WHY TEMPLATE???????
template <typename Key>
Crypto::AESCipher<Key>::AESCipher(Key key, Mode mode) : ctx(std::make_unique<CipherContext>()) {
    ctx->encryption_context = EVP_CIPHER_CTX_new();
    ctx->decryption_context = EVP_CIPHER_CTX_new();
    ctx->cipher = GetCipher(mode, sizeof(Key));
    if (ctx->cipher) {
        EVP_CIPHER_up_ref(ctx->cipher);
    } else {
        UNIMPLEMENTED();
    }

    ASSERT(ctx->encryption_context && ctx->decryption_context && ctx->cipher && "OpenSSL cipher context failed init!");
    // now init ciphers
    ASSERT(EVP_CipherInit_ex2(ctx->encryption_context, ctx->cipher, key.data(), NULL, 1, NULL));
    ASSERT(EVP_CipherInit_ex2(ctx->decryption_context, ctx->cipher, key.data(), NULL, 0, NULL));

    EVP_CIPHER_CTX_set_padding(ctx->encryption_context, 0);
    EVP_CIPHER_CTX_set_padding(ctx->decryption_context, 0);

#ifdef EDEN_AES_ARMV8_CTR
    if constexpr (sizeof(Key) == 16) {
        if (mode == Mode::CTR && (getauxval(AT_HWCAP) & HWCAP_AES) != 0) {
            ctx->round_keys = ExpandKey128(key.data());
            ctx->armv8_ctr = true;
        }
    }
#endif
}

template <typename Key>
AESCipher<Key>::~AESCipher() {
    EVP_CIPHER_CTX_free(ctx->encryption_context);
    EVP_CIPHER_CTX_free(ctx->decryption_context);
    EVP_CIPHER_free(ctx->cipher);
}

template <typename Key>
void AESCipher<Key>::Transcode(const u8* src, std::size_t size, u8* dest, Op op) const {
    if (size == 0)
        return;

#ifdef EDEN_AES_ARMV8_CTR
    if (ctx->armv8_ctr) {
        Armv8Ctr(ctx->round_keys, op == Op::Encrypt ? ctx->encryption_counter : ctx->decryption_counter, src,
                 size, dest);
        return;
    }
#endif

    auto* const context = op == Op::Encrypt ? ctx->encryption_context : ctx->decryption_context;

    // reset
    ASSERT(EVP_CipherInit_ex(context, nullptr, nullptr, nullptr, nullptr, -1));

    const int block_size = EVP_CIPHER_CTX_get_block_size(context);
    ASSERT(block_size > 0 && block_size <= int(AesBlockBytes));

    const std::size_t whole_block_bytes = size - (size % block_size);
    int written = 0;

    if (whole_block_bytes != 0) {
        ASSERT(EVP_CipherUpdate(context, dest, &written, src, static_cast<int>(whole_block_bytes)));

        if (std::size_t(written) != whole_block_bytes) {
            LOG_WARNING(Crypto, "Not all data was processed requested={:016X}, actual={:016X}.",
                        whole_block_bytes, written);
        }
    }

    // tail
    const std::size_t tail = size - whole_block_bytes;
    if (tail == 0)
        return;

    std::array<u8, AesBlockBytes> tail_buffer{};
    std::memcpy(tail_buffer.data(), src + whole_block_bytes, tail);

    int tail_written = 0;

    ASSERT(EVP_CipherUpdate(context, tail_buffer.data(), &tail_written, tail_buffer.data(), block_size));

    if (tail_written != block_size) {
        LOG_WARNING(Crypto, "Tail block not fully processed requested={:016X}, actual={:016X}.",
                    block_size, tail_written);
    }

    std::memcpy(dest + whole_block_bytes, tail_buffer.data(), tail);
}

template <typename Key>
void AESCipher<Key>::XTSTranscode(const u8* src, std::size_t size, u8* dest, std::size_t sector_id, std::size_t sector_size, Op op) {
    ASSERT(size % sector_size == 0 && "XTS decryption size must be a multiple of sector size.");
    for (std::size_t i = 0; i < size; i += sector_size) {
        SetIV(CalculateNintendoTweak(sector_id++));
        Transcode(src + i, sector_size, dest + i, op);
    }
}

template <typename Key>
void AESCipher<Key>::SetIV(std::span<const u8> data) {
#ifdef EDEN_AES_ARMV8_CTR
    if (ctx->armv8_ctr) {
        ASSERT(data.size() == ctx->encryption_counter.size());
        std::memcpy(ctx->encryption_counter.data(), data.data(), data.size());
        std::memcpy(ctx->decryption_counter.data(), data.data(), data.size());
        return;
    }
#endif
    const int ret_enc = EVP_CipherInit_ex(ctx->encryption_context, nullptr, nullptr, nullptr, data.data(), -1);
    const int ret_dec = EVP_CipherInit_ex(ctx->decryption_context, nullptr, nullptr, nullptr, data.data(), -1);
    ASSERT(ret_enc == 1 && ret_dec == 1 && "Failed to set IV on OpenSSL contexts");
}

template class AESCipher<Key128>;
template class AESCipher<Key256>;
} // namespace Core::Crypto
