/*
Copyright (c) 2011, Jonas Tarnstrom and ESN Social Software AB
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.
3. All advertising materials mentioning features or use of this software
must display the following acknowledgement:
This product includes software developed by ESN Social Software AB (www.esn.me).
4. Neither the name of the ESN Social Software AB nor the
names of its contributors may be used to endorse or promote products
derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY ESN SOCIAL SOFTWARE AB ''AS IS'' AND ANY
EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL ESN SOCIAL SOFTWARE AB BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Portions of code from gevent-MySQL
Copyright (C) 2010, Markus Thurlin
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
this list of conditions and the following disclaimer in the documentation
and/or other materials provided with the distribution.

* Neither the name of Hyves (Startphone Ltd.) nor the names of its
contributors may be used to endorse or promote products derived from this
software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

*/
#include "PacketReader.h"
#include "mysqldefs.h"
#include <assert.h>
#include "socketdefs.h"

#define BYTEORDER_UINT16(_x) (_x)
#define BYTEORDER_UINT32(_x) (_x)

PacketReader::PacketReader (size_t _cbSize)
{
  m_buffStart = new char[_cbSize];
  m_writeCursor = m_buffStart;
  m_buffEnd = m_buffStart + _cbSize;
  m_readCursor = m_buffStart;
  m_packetEnd = NULL;
  m_overflow = false;
}

PacketReader::~PacketReader (void)
{
  delete[] m_buffStart;
}

void PacketReader::skip()
{
  assert (m_packetEnd != NULL);
  assert (m_readCursor <= m_packetEnd);

  m_readCursor = m_packetEnd;

  if (m_readCursor == m_writeCursor)
  {
    //fprintf (stderr, "%s: Buffer is aligned, moving back\n", __FUNCTION__);

    m_readCursor = m_buffStart;
    m_writeCursor = m_buffStart;
    m_packetEnd = NULL;
  }
}

void PacketReader::push(size_t _cbData)
{
  //fprintf (stderr, "%s: Pushing %u bytes\n", __FUNCTION__, _cbData);
  m_writeCursor += _cbData;
}

char *PacketReader::getWritePtr()
{
  return m_writeCursor;
}

char *PacketReader::getStartPtr()
{
  return m_buffStart;
}

char *PacketReader::getEndPtr()
{
  return m_buffEnd;
}

extern void PrintBuffer(FILE *file, void *_offset, size_t len, int perRow);


// Untrusted-input bounds. The asserts in the read primitives are stripped under
// NDEBUG (the normal release/pip build), so each primitive routes through
// ensure(): a read that would pass the packet end (or, while the 4-byte header
// is being parsed, the received-data write cursor) is refused. The reader latches
// an overflow flag and returns safe zero/NULL; callers check overflowed() and
// abort the packet, and individual NULL returns are also checked at call sites.
bool PacketReader::ensure(size_t n)
{
  char *limit = (m_packetEnd != NULL) ? m_packetEnd : m_writeCursor;
  if (m_overflow || m_readCursor > limit || n > (size_t) (limit - m_readCursor))
  {
    m_overflow = true;
    return false;
  }
  return true;
}

bool PacketReader::overflowed()
{
  return m_overflow;
}

bool PacketReader::havePacket()
{
  m_packetEnd = NULL;
  m_overflow = false;

  size_t len = (m_writeCursor - m_readCursor);

  if (len < MYSQL_PACKET_HEADER_SIZE)
  {
    return false;
  }

  UINT32 packetSize = readINT24();
  UINT32 packetNumber = readByte();

  if (len < MYSQL_PACKET_HEADER_SIZE + packetSize)
  {
    m_readCursor -= 4;
    //fprintf (stderr, "%s: Not enough bytes in buffer, have %u, want %u\n", __FUNCTION__, len, MYSQL_PACKET_HEADER_SIZE + packetSize);
    return false;
  }

  this->m_packetEnd = m_readCursor + packetSize;

  //fprintf (stderr, "%s: Have a packet %02x\n", __FUNCTION__, (*m_readCursor));
  //PrintBuffer (stderr, m_readCursor, (m_packetEnd - m_readCursor), 16);

  return true;
}

UINT8 PacketReader::readByte()
{
  if (!ensure(1)) return 0;
  return (*m_readCursor++);
}

UINT16 PacketReader::readShort()
{
  if (!ensure(2)) return 0;
  UINT16 ret = BYTEORDER_UINT16(*((UINT16*)m_readCursor));
  m_readCursor += 2;
  return ret;
}

UINT32 PacketReader::readINT24()
{
  if (!ensure(3)) return 0;
  UINT32 ret = readByte() | (readByte() << 8) | (readByte() << 16);

  return ret;
}

UINT32 PacketReader::readLong()
{
  if (!ensure(4)) return 0;
  UINT32 ret = BYTEORDER_UINT32(*((UINT32*)m_readCursor));
  m_readCursor += 4;
  return ret;
}

char *PacketReader::readNTString()
{
  char *ret = m_readCursor;

  while (m_readCursor < m_packetEnd)
  {
    if (*(m_readCursor++) == '\0')
    {
      return ret;
    }
  }

  // No NUL terminator inside the packet -- malformed/hostile. Flag and return
  // NULL so callers do not consume an unterminated string.
  m_overflow = true;
  return NULL;
}


UINT8 *PacketReader::readBytes(size_t cbsize)
{
  if (!ensure(cbsize)) return NULL;

  UINT8 *ret = (UINT8 *) m_readCursor;
  m_readCursor += cbsize;

  return ret;
}

size_t PacketReader::getBytesLeft()
{
  return (m_packetEnd - m_readCursor);
}

void PacketReader::rewind(size_t num)
{
  m_readCursor -= num;
}


UINT8 *PacketReader::readLengthCodedBinary(size_t *_outLen)
{
  // Untrusted input. The asserts below are stripped under NDEBUG (the normal
  // release/pip build), so every read is validated against the packet end at
  // runtime here. A malformed/hostile packet yields NULL/truncated data rather
  // than an out-of-bounds read (memory disclosure / crash).
  assert (m_packetEnd <= m_writeCursor);

  if (m_readCursor >= m_packetEnd)
  {
    *_outLen = 0;
    return NULL;
  }

  switch (*((UINT8 *) m_readCursor))
  {
  default:
    *_outLen = (size_t) *((UINT8 *) m_readCursor);
    m_readCursor ++;
    break;

  case 251:
    m_readCursor ++;
    *_outLen = 0;
    return NULL;

  case 252:
    if (m_readCursor + 3 > m_packetEnd) { m_readCursor = m_packetEnd; *_outLen = 0; return NULL; }
    m_readCursor ++;
    *_outLen = (size_t) *((UINT16 *) m_readCursor);
    m_readCursor += 2;
    break;

  case 253:
    if (m_readCursor + 4 > m_packetEnd) { m_readCursor = m_packetEnd; *_outLen = 0; return NULL; }
    m_readCursor ++;
    *_outLen = (size_t) *((UINT32 *) m_readCursor);
    *_outLen &= 0xffffff;
    m_readCursor += 3;
    break;

  case 254:
    if (m_readCursor + 9 > m_packetEnd) { m_readCursor = m_packetEnd; *_outLen = 0; return NULL; }
    m_readCursor ++;
    *_outLen = (size_t) *((UINT64 *) m_readCursor);
    m_readCursor += 8;
    break;
  }

  // Clamp the payload length to the bytes actually remaining in the packet, so
  // the returned (pointer, length) can never reference memory past m_packetEnd.
  {
    size_t avail = (size_t) (m_packetEnd - m_readCursor);
    if (*_outLen > avail)
    {
      *_outLen = avail;
    }
  }

  UINT8 *ret = (UINT8*) m_readCursor;
  m_readCursor += (*_outLen);

  return ret;
}

size_t PacketReader::getSize()
{
  return m_buffEnd - m_buffStart;
}


UINT64 PacketReader::readLengthCodedInteger()
{
  UINT64 ret;

  if (!ensure(1)) return 0;

  switch (*((UINT8 *) m_readCursor))
  {
  default:
    ret = (UINT64) *((UINT8 *) m_readCursor);
    m_readCursor ++;
    return ret;

  case 251:
    ret = 0;
    m_readCursor ++;
    return ret;

  case 252:
    if (!ensure(3)) return 0;
    m_readCursor ++;
    ret = (UINT64) *((UINT16 *) m_readCursor);
    m_readCursor += 2;
    return ret;

  case 253:
    if (!ensure(4)) return 0;
    m_readCursor ++;
    ret = (UINT64) *((UINT32 *) m_readCursor);
    ret &= 0xffffff;
    m_readCursor += 3;
    return ret;

  case 254:
    if (!ensure(9)) return 0;
    m_readCursor ++;
    ret = (UINT64) *((UINT64 *) m_readCursor);
    m_readCursor += 8;
    return ret;
  }

  return ret;
}



