# .elo File Format Specification
### EloAI Semantic Compression Format — v1.0

> ⚠ **DUPLICATE COPY (2026-08-19).** The `.elo` file-format lane
> (`elo-file-format/docs/spec/ELO_FILE_FORMAT.md`) owns this spec; four copies
> exist across the repo and have diverged (the `Memory/` copies carried a claim
> refuted by measurement that the others never had). Treat this copy as local
> reference for the compression lane; reconcile any change in the owner's copy.
> A lossless binary document format where compression and semantic intelligence are the same operation.

---

## Overview

The `.elo` format is a binary document format built on the EloAI Base64 canonical dictionary. It replaces verbose text formats (JSON, XML, plain text) with compressed ID streams that are simultaneously smaller, faster to process, and semantically queryable without any additional NLP pipeline.

**Core properties:**
- 100% lossless — every document perfectly reconstructable
- 85-92% smaller than equivalent JSON or plain text
- Semantically queryable at the storage layer
- Dictionary-based — 14MB shared dictionary covers all content
- Memory-mappable — entire corpus fits in RAM
- Appendable — no full file rewrites required

---

## Design Principles

```
1. COMPRESSION = SEMANTICS
   An ID is not just a shorter representation of a phrase.
   It IS the semantic classification of that phrase.
   Compression and meaning are the same operation.

2. DICTIONARY IS SHARED
   One 14MB dictionary serves all .elo files.
   A phrase compressed in one file uses the same ID
   as the same phrase in every other file.
   Cross-document semantic linking is structural, not computed.

3. LOSSLESS BY DESIGN
   OOV (out-of-vocabulary) content stored as raw text with marker.
   No content is ever lost or approximated.
   decode(encode(x)) == x. Always.

4. BINARY FIRST
   Not human-readable by default.
   Human-readable via decode layer.
   Storage and processing optimized for machines.
```

---

## File Structure

```
┌─────────────────────────────────────────┐
│  HEADER          64 bytes (fixed)        │
├─────────────────────────────────────────┤
│  SCHEMA          variable               │
│  (field definitions for this file type) │
├─────────────────────────────────────────┤
│  INDEX           variable               │
│  (record_id → byte_offset mapping)      │
├─────────────────────────────────────────┤
│  RECORDS         variable               │
│  (compressed data records)              │
├─────────────────────────────────────────┤
│  STRING TABLE    variable               │
│  (compressed entity names, titles etc)  │
├─────────────────────────────────────────┤
│  METADATA        variable               │
│  (file-level semantic profile)          │
└─────────────────────────────────────────┘
```

---

## Header Specification

Fixed 64-byte header. Always at byte offset 0.

```
Offset  Size  Type      Field               Description
──────────────────────────────────────────────────────────────
0       4     char[4]   magic               Always "ELO1"
4       4     uint32    dict_version        Dictionary version used to encode
8       4     uint32    format_version      .elo format version (currently 1)
12      4     uint32    record_count        Total number of records
16      8     uint64    index_offset        Byte offset of INDEX section
24      8     uint64    string_table_offset Byte offset of STRING TABLE
32      8     uint64    metadata_offset     Byte offset of METADATA section
40      8     uint64    created_at          Unix timestamp (seconds)
48      8     uint64    content_checksum    xxHash64 of RECORDS section
56      4     uint32    flags               Feature flags (see below)
60      4     uint32    reserved            Must be 0x00000000
```

**Flags field (bit flags):**
```
Bit 0:  HAS_SEMANTIC_PROFILE   metadata includes Layer 2 semantic data
Bit 1:  HAS_TIMESTAMPS         records include time offsets
Bit 2:  HAS_SPEAKERS           records include speaker IDs
Bit 3:  APPENDABLE             file supports append-only writes
Bit 4:  ENCRYPTED              content is encrypted (future)
Bits 5-31: reserved, must be 0
```

---

## ID Encoding

All text content is encoded using the EloAI Base64 dictionary.

```
Charset:  ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_
          (URL-safe Base64, 64 characters)

ID lengths and their meaning:
  1-char:   top 50 universal words + system primitives
  2-char:   next 4,046 most frequent units
  3-char:   up to 262,144 units (primary dictionary — 14MB)
  OOV:      raw UTF-8 text prefixed with 0xFF marker byte

ID delimiter in streams:
  0x1F  (ASCII Unit Separator) between IDs
  0x1E  (ASCII Record Separator) between logical segments
```

**OOV (Out-of-Vocabulary) encoding:**
```
When a word or phrase has no dictionary ID:
  [0xFF][uint16 length][UTF-8 bytes]

This guarantees 100% lossless encoding of any content
regardless of dictionary coverage.
```

---

## Schema Section

Defines the field structure for records in this file. Allows .elo to represent any document type.

```
Schema entry per field:
  field_id:     uint8       (0-255, unique within file)
  field_name:   string_ref  (reference into STRING TABLE)
  field_type:   uint8       (see Field Types below)
  flags:        uint8       (nullable, indexed, etc.)
```

**Field Types:**
```
0x01  ID_STREAM     compressed text (sequence of dictionary IDs)
0x02  UINT8         unsigned 8-bit integer
0x03  UINT16        unsigned 16-bit integer
0x04  UINT32        unsigned 32-bit integer
0x05  UINT64        unsigned 64-bit integer
0x06  FLOAT32       32-bit float
0x07  FLOAT64       64-bit float
0x08  STRING_REF    reference into STRING TABLE
0x09  BYTES         raw binary data with uint32 length prefix
0x0A  BOOL          single byte, 0x00 or 0x01
```

---

## Record Format

Each record is a sequence of field values matching the schema.

```
Record structure:
  [uint32 record_id]
  [uint32 byte_length]     length of this record in bytes
  [field values...]        one per schema field, in schema order

Field value encoding:
  ID_STREAM fields:
    [uint32 stream_length]  number of bytes in stream
    [bytes...]              sequence of 1-3 byte IDs + OOV sequences
    delimited by 0x1F between IDs

  Integer/float fields:
    little-endian binary, fixed width per type

  STRING_REF fields:
    [uint32 string_table_offset]
```

---

## Transcript Record Schema (Primary Use Case)

The standard schema for YouTube transcript data.

```
Field ID  Name          Type        Description
──────────────────────────────────────────────────────
0x01      record_type   UINT8       0x01=chunk, 0x02=metadata
0x02      video_id      STRING_REF  YouTube video ID
0x03      channel_id    UINT16      Channel integer ID
0x04      chunk_id      UINT32      Sequential chunk number
0x05      start_sec     UINT32      Start time in seconds
0x06      end_sec       UINT32      End time in seconds
0x07      speaker_id    UINT16      Speaker reference (0=unknown)
0x08      text          ID_STREAM   Compressed transcript text
0x09      flags         UINT8       Bit flags (validated, etc.)
```

**Example — current JSON vs .elo binary:**

```
Current JSON (380 bytes):
{
  "video_id": "-_DjOrSvYLo",
  "channel_id": 2,
  "chunk_id": 0,
  "start": 0,
  "end": 34,
  "speaker": "unknown",
  "text": "this is jocko podcast number 279 with echo charles..."
}

.elo binary (~48 bytes):
  [0x00000000]           record_id
  [0x00000030]           byte_length = 48
  [0x01]                 record_type = chunk
  [0x00000012]           video_id → string_table[18]
  [0x0002]               channel_id = 2
  [0x00000000]           chunk_id = 0
  [0x00000000]           start_sec = 0
  [0x00000022]           end_sec = 34
  [0x0000]               speaker_id = 0 (unknown)
  [0x0000001E][IDs...]   text ID stream (30 bytes)
  [0x00]                 flags

Savings: 87% smaller than JSON
```

---

## Index Section

Enables O(1) random access to any record by ID.

```
Index structure:
  [uint32 entry_count]
  entries[]:
    [uint32 record_id]
    [uint64 byte_offset]   absolute offset from file start

Sorted by record_id for binary search.
Memory-mappable — OS handles caching.
Lookup: O(log n) binary search → O(1) seek
```

---

## String Table

Stores all string values (video IDs, titles, channel names, speaker names) compressed and deduplicated.

```
String table structure:
  [uint32 entry_count]
  entries[]:
    [uint32 string_id]
    [uint16 byte_length]
    [bytes...]           UTF-8 encoded string

Strings referenced by index from records.
Each unique string stored exactly once.
Dramatically reduces repetition of video_id, channel_name, etc.
```

---

## Metadata Section

File-level information and optional Layer 2 semantic profile.

```
Metadata structure:
  [uint32 entry_count]
  entries[]:
    [uint8 key_id]
    [uint8 value_type]
    [bytes...]  value

Standard metadata keys:
  0x01  TITLE           string    human-readable file description
  0x02  SOURCE          string    origin (youtube, web, etc.)
  0x03  CREATED_BY      string    pipeline version
  0x04  DICT_CHECKSUM   uint64    xxHash64 of dictionary used
  0x05  RECORD_SCHEMA   uint8     schema version identifier

Layer 2 semantic profile (flag HAS_SEMANTIC_PROFILE):
  0x10  DOMINANT_STAGE  uint8     most common process stage ID
  0x11  TOP_THEMES      bytes     top 5 theme IDs (5 × 3 bytes)
  0x12  EMOTION_PROFILE bytes     EPA float triple [e, p, a]
  0x13  CERTAINTY_AVG   float32   average certainty across content
  0x14  FILLER_DENSITY  float32   filler tokens / total tokens
```

---

## Storage Size Estimates

```
Content Type         JSON / Text    .elo       Reduction
──────────────────────────────────────────────────────────
Transcript corpus    1.3 GB         ~150 MB    88%
Single transcript    1.5 MB         ~180 KB    88%
Single chunk         ~380 bytes     ~48 bytes  87%
Novel (300 pages)    1.2 MB         ~150 KB    88%
Academic paper       800 KB         ~100 KB    88%
Wikipedia dump       21 GB          ~2.5 GB    88%
```

---

## LMDB Storage Layout

When stored as LMDB (recommended for production):

```
Database: transcripts.lmdb

Tables:
  records       key: record_id (uint32)
                val: compressed record bytes

  index         key: video_id (string)
                val: [record_id, ...] list

  string_table  key: string_id (uint32)
                val: UTF-8 string bytes

  metadata      key: key_id (uint8)
                val: value bytes

  dict_version  key: "version"
                val: uint32 dictionary version

Performance:
  Read:   ~100 nanoseconds per record lookup
  Write:  ~200 nanoseconds per record insert
  RAM:    ~150 MB for full 1.3GB transcript corpus
  Fits entirely in memory on any modern machine
```

---

## Dictionary Versioning

The dictionary evolves as the corpus grows. Version management ensures files remain decodable.

```
Dictionary versions:
  v1:  initial build from transcript corpus
  v2:  expanded with web corpus
  v3+: domain extensions

Version compatibility:
  Files encoded with dict v1 always decodable with dict v1
  Newer dictionary versions are additive only
  No IDs are ever reassigned
  Old files never break

Version stored in:
  File header:    dict_version field
  LMDB metadata:  dict_version table
```

---

## Encode / Decode API

```python
# Python reference implementation

class EloFile:
    
    def encode(self, text: str) -> bytes:
        """
        Encode UTF-8 text to .elo ID stream.
        100% lossless — OOV content preserved with 0xFF marker.
        """
        pass
    
    def decode(self, stream: bytes) -> str:
        """
        Decode .elo ID stream back to original UTF-8 text.
        Perfect reconstruction guaranteed.
        """
        pass
    
    def write_transcript(self, path: str, transcript: dict) -> None:
        """Write a transcript JSON as .elo binary."""
        pass
    
    def read_transcript(self, path: str) -> dict:
        """Read .elo file back to transcript dict. Identical to original."""
        pass
    
    def read_chunk(self, path: str, chunk_id: int) -> dict:
        """O(1) random access to specific chunk. No sequential scan."""
        pass
    
    def search_id(self, path: str, dictionary_id: str) -> list:
        """
        Find all records containing a specific dictionary ID.
        Binary scan — no text parsing required.
        Returns list of (record_id, byte_offset) tuples.
        """
        pass
    
    def semantic_profile(self, path: str) -> dict:
        """
        Return Layer 2 semantic profile if HAS_SEMANTIC_PROFILE flag set.
        {dominant_stage, top_themes, emotion_profile, certainty_avg}
        """
        pass
```

---

## Migration Path From JSON

```
Step 1:  Build elo_encoder.py
         Input:  existing JSON transcript files
         Output: .elo binary files
         Verify: round_trip_test() passes on all files

Step 2:  Verify lossless conversion
         decode(encode(json)) == original_json
         Must be 100% before proceeding

Step 3:  Migrate existing corpus
         1.3 GB JSON → ~150 MB .elo
         Keep JSON as archive backup

Step 4:  Update pipeline
         All downstream tools read .elo directly
         JSON becomes import-only format

Step 5:  New transcripts written directly to .elo
         JSON pipeline retired
```

---

## Future Extensions

```
.elo v2 planned features:

DIFFERENTIAL STORAGE
  Two similar documents share IDs
  Store only the delta between versions
  Git-style versioning at semantic granularity

ENCRYPTION
  AES-256 content encryption
  Dictionary IDs reveal nothing without key
  Flags bit 4 reserved for this

STREAMING
  Write records as they arrive
  No buffering required
  Live transcript encoding in real time

MULTI-DICTIONARY
  Domain-specific dictionary extensions
  Medical, legal, technical vocabularies
  Stacked on top of base dictionary

CROSS-FILE LINKING
  Record in file A references record in file B
  By dictionary ID — not by text
  Semantic graph across entire corpus
```

---

## Reference

```
EloAI                 https://eloai.dev
Dictionary spec       ../../CLAUDE.md (root R-D-concepts) → Base64 ID System
System 1 spec         ../SYSTEM1.md → Canonical Library Build
Compression theory    Surov (2022) Quantum Core Affect
Repository            github.com/4waymedia/semantic-compression

File extension:       .elo
MIME type:            application/x-elo
Magic bytes:          45 4C 4F 31  ("ELO1")
Endianness:           little-endian throughout
```
