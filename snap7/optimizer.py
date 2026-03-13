"""
Read optimization pipeline for S7 multi-variable reads.

Implements the nodeS7-style 3-stage optimization:
  Sort → Merge (with max gap) → Packetize (fit PDU)

This collapses many scattered read requests into a minimal number of
PDU-packed network exchanges.
"""

from dataclasses import dataclass, field
from typing import List, Tuple

from .type import Area

# Areas that support contiguous-block merging.
# CT and TM use different addressing semantics and are excluded.
OPTIMIZABLE_AREAS = frozenset({Area.PE, Area.PA, Area.MK, Area.DB})


@dataclass
class ReadItem:
    """A single user-requested read variable."""

    area: Area
    db_number: int  # 0 for non-DB areas
    byte_offset: int
    bit_offset: int  # 0-7, relevant for BOOLs
    byte_length: int
    index: int  # Original position in user's list (for result mapping)


@dataclass
class ReadBlock:
    """A merged contiguous block of bytes to read in one address spec."""

    area: Area
    db_number: int
    start_offset: int
    byte_length: int
    items: List[ReadItem] = field(default_factory=list)
    buffer: bytearray = field(default_factory=bytearray)


@dataclass
class ReadPacket:
    """A set of blocks that fit within a single S7 PDU."""

    blocks: List[ReadBlock] = field(default_factory=list)


def sort_items(items: List[ReadItem]) -> List[ReadItem]:
    """Sort read items for optimal merging.

    Sort key: (area.value, db_number, byte_offset, bit_offset, -byte_length).
    Sorting by descending byte_length ensures that when two items start at the
    same offset, the larger one comes first and drives the merged block size.
    """
    return sorted(
        items,
        key=lambda item: (
            item.area.value,
            item.db_number,
            item.byte_offset,
            item.bit_offset,
            -item.byte_length,
        ),
    )


def merge_items(
    sorted_items: List[ReadItem],
    max_gap: int,
    max_byte_request: int,
) -> List[ReadBlock]:
    """Merge sorted items into contiguous read blocks.

    A new block is started when:
    - The area changes
    - The DB number changes
    - The area is not optimizable (CT/TM)
    - The gap between the current block end and the next item exceeds *max_gap*
    - The merged length would exceed *max_byte_request*
    """
    if not sorted_items:
        return []

    blocks: List[ReadBlock] = []
    current_block: ReadBlock | None = None

    for item in sorted_items:
        if current_block is None or not _can_merge(current_block, item, max_gap, max_byte_request):
            current_block = ReadBlock(
                area=item.area,
                db_number=item.db_number,
                start_offset=item.byte_offset,
                byte_length=item.byte_length,
                items=[item],
            )
            blocks.append(current_block)
        else:
            # Extend the block to cover this item
            item_end = item.byte_offset + item.byte_length
            block_end = current_block.start_offset + current_block.byte_length
            new_end = max(block_end, item_end)
            current_block.byte_length = new_end - current_block.start_offset
            current_block.items.append(item)

    return blocks


def _can_merge(block: ReadBlock, item: ReadItem, max_gap: int, max_byte_request: int) -> bool:
    """Check if *item* can be merged into *block*."""
    if item.area != block.area:
        return False
    if item.db_number != block.db_number:
        return False
    if item.area not in OPTIMIZABLE_AREAS:
        return False

    block_end = block.start_offset + block.byte_length
    gap = item.byte_offset - block_end

    if gap > max_gap:
        return False

    # Compute what the merged length would be
    item_end = item.byte_offset + item.byte_length
    new_end = max(block_end, item_end)
    merged_length = new_end - block.start_offset

    if merged_length > max_byte_request:
        return False

    return True


def packetize(blocks: List[ReadBlock], pdu_size: int) -> List[ReadPacket]:
    """Pack blocks into PDU-sized packets.

    Each S7 read response PDU has two budgets:
      - **request_length**: starts at 12 (S7 header), each block adds 12 bytes
        (one address spec).
      - **reply_length**: starts at 14 (S7 header + param), each block adds
        ``byte_length_with_fill + 4`` (4-byte item header + data + fill).
      - ``byte_length_with_fill = byte_length + (byte_length % 2)``
        (2-byte alignment padding between response items).

    Both budgets must stay ≤ *pdu_size*.
    """
    if not blocks:
        return []

    max_byte_request = _max_byte_request(pdu_size)

    # Split oversized blocks first
    split_blocks: List[ReadBlock] = []
    for block in blocks:
        if block.byte_length > max_byte_request:
            split_blocks.extend(_split_block(block, max_byte_request))
        else:
            split_blocks.append(block)

    packets: List[ReadPacket] = []
    current_packet = ReadPacket()
    # S7 header (12) + param header function+count (2) = 14 for reply
    # S7 header (12) for request param section
    reply_budget = 14
    request_budget = 12

    for block in split_blocks:
        fill = block.byte_length % 2
        block_reply_cost = block.byte_length + fill + 4  # 4-byte item header
        block_request_cost = 12  # One 12-byte address spec

        new_reply = reply_budget + block_reply_cost
        new_request = request_budget + block_request_cost

        if current_packet.blocks and (new_reply > pdu_size or new_request > pdu_size):
            # Current packet is full; start a new one
            packets.append(current_packet)
            current_packet = ReadPacket()
            reply_budget = 14
            request_budget = 12

        current_packet.blocks.append(block)
        reply_budget += block_reply_cost
        request_budget += block_request_cost

    if current_packet.blocks:
        packets.append(current_packet)

    return packets


def _split_block(block: ReadBlock, max_byte_request: int) -> List[ReadBlock]:
    """Split an oversized block into smaller blocks that fit *max_byte_request*."""
    result: List[ReadBlock] = []
    offset = block.start_offset
    remaining = block.byte_length

    while remaining > 0:
        chunk_size = min(remaining, max_byte_request)
        chunk_end = offset + chunk_size

        # Gather items that fall within this chunk
        chunk_items = [
            item for item in block.items if item.byte_offset < chunk_end and item.byte_offset + item.byte_length > offset
        ]

        result.append(
            ReadBlock(
                area=block.area,
                db_number=block.db_number,
                start_offset=offset,
                byte_length=chunk_size,
                items=chunk_items,
            )
        )

        offset += chunk_size
        remaining -= chunk_size

    return result


def _max_byte_request(pdu_size: int) -> int:
    """Maximum contiguous read size for a single address spec.

    ``4 * ((pdu_size - 18) // 4)`` — 4-byte aligned to avoid splitting
    REAL/DINT values across PDU boundaries.
    """
    return 4 * ((pdu_size - 18) // 4)


def optimize_reads(
    items: List[ReadItem],
    pdu_size: int,
    max_gap: int = 5,
) -> List[ReadPacket]:
    """Run the full 3-stage optimization pipeline.

    Args:
        items: User-requested read items.
        pdu_size: Negotiated PDU size (e.g. 240, 480, 960).
        max_gap: Maximum gap in bytes between items that can still be merged.

    Returns:
        List of packets ready to be dispatched as multi-item S7 reads.
    """
    if not items:
        return []

    max_byte_req = _max_byte_request(pdu_size)
    sorted_ = sort_items(items)
    blocks = merge_items(sorted_, max_gap, max_byte_req)
    return packetize(blocks, pdu_size)


def extract_results(
    items: List[ReadItem],
    blocks: List[ReadBlock],
) -> List[bytearray]:
    """Extract each original item's bytes from its block's buffer.

    Returns a list of bytearrays in the same order as the original *items*.
    """
    results: List[Tuple[int, bytearray]] = []

    for block in blocks:
        for item in block.items:
            local_offset = item.byte_offset - block.start_offset
            data = bytearray(block.buffer[local_offset : local_offset + item.byte_length])
            results.append((item.index, data))

    # Sort by original index and return
    results.sort(key=lambda x: x[0])
    return [data for _, data in results]
