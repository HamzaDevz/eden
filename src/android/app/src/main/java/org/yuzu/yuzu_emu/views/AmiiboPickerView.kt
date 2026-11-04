// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.views

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import kotlin.math.PI
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * The amiibo a game waits for (a FIG card in Smash, for one), offered over the game the way Smash
 * draws its own select screens: a yellow banner, then a row of slanted cards with the fighter's
 * portrait, the nickname and the level. Left and right move through them, A picks one and B
 * closes (pick(), move(), close()), or a card is touched.
 */
class AmiiboPickerView(context: Context) : View(context) {
    class Entry(val name: String, val level: Int?, val portrait: Bitmap?)

    var onPick: (Int) -> Unit = {}
    var onClose: () -> Unit = {}

    private var entries = listOf<Entry>()
    private var title = ""
    private var levelLabel = ""
    private var pickLabel = ""
    private var backLabel = ""
    private var selected = 0
    private var focus = FloatArray(0) // each card's highlight, easing to 1 on the selected one
    private var shownAt = 0L
    private var lastFrame = 0L
    private val cards = mutableListOf<RectF>() // hit boxes, in view coordinates

    private val heavy = Typeface.create("sans-serif-black", Typeface.ITALIC)
    private val backdrop = Paint().apply { color = Color.BLACK }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rim = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
    }
    private val image = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = heavy
        textAlign = Paint.Align.CENTER
    }
    private val outline = Paint(text).apply {
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
        color = Color.BLACK
    }
    private val path = Path()
    private val box = RectF()
    private val ticker = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 1000
        repeatCount = ValueAnimator.INFINITE
        addUpdateListener { invalidate() }
    }

    init {
        isClickable = true
        isFocusable = false
    }

    fun show(entries: List<Entry>, title: String, level: String, pick: String, back: String) {
        this.entries = entries
        this.title = title
        levelLabel = level
        pickLabel = pick
        backLabel = back
        selected = 0
        focus = FloatArray(entries.size).also { if (it.isNotEmpty()) it[0] = 1f }
        shownAt = SystemClock.uptimeMillis()
        lastFrame = shownAt
        invalidate()
    }

    fun move(step: Int) {
        if (entries.isNotEmpty()) {
            selected = (selected + step).mod(entries.size)
        }
    }

    fun pick() {
        if (selected in entries.indices) {
            onPick(selected)
        }
    }

    fun close() = onClose()

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        ticker.start()
    }

    override fun onDetachedFromWindow() {
        ticker.cancel()
        super.onDetachedFromWindow()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_UP) {
            val hit = cards.indexOfFirst { it.contains(event.x, event.y) }
            if (hit >= 0) {
                selected = hit
                pick()
            } else {
                close()
            }
        }
        return true
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) {
            return
        }
        val now = SystemClock.uptimeMillis()
        val dt = (now - lastFrame).coerceIn(0L, 50L) / 1000f
        lastFrame = now
        for (i in focus.indices) {
            val target = if (i == selected) 1f else 0f
            focus[i] += (target - focus[i]) * min(1f, dt * 16f)
        }
        val appear = easeOut((now - shownAt) / 220f)
        val pulse = 0.5f + 0.5f * sin((now % 1200L) / 1200f * 2f * PI.toFloat())

        backdrop.alpha = (175 * appear).toInt()
        canvas.drawRect(0f, 0f, w, h, backdrop)
        drawBanner(canvas, w, h, appear)

        val n = max(entries.size, 1)
        val cw = min(w * 0.86f / (n + (n - 1) * 0.1f), h * 0.3f)
        val ch = cw * 1.42f
        val gap = cw * 0.1f
        val left = (w - n * cw - (n - 1) * gap) / 2f
        val cy = h * 0.53f
        cards.clear()
        for ((i, entry) in entries.withIndex()) {
            // Each card slides up a little after the one on its left
            val a = easeOut((now - shownAt - i * 45L) / 260f)
            val f = focus[i]
            val scale = 1f + 0.09f * f
            val cx = left + i * (cw + gap) + cw / 2f
            val y = cy + (1f - a) * h * 0.2f - f * ch * 0.03f
            cards += RectF(cx - cw / 2f, y - ch / 2f, cx + cw / 2f, y + ch / 2f)
            canvas.save()
            canvas.translate(cx, y)
            canvas.scale(scale, scale)
            drawCard(canvas, entry, cw, ch, f, pulse, a)
            canvas.restore()
        }
        drawHints(canvas, w, h, appear)
    }

    private fun drawBanner(canvas: Canvas, w: Float, h: Float, appear: Float) {
        val bh = h * 0.085f
        val bw = w * 0.5f
        val x = (w - bw) / 2f
        val y = h * 0.07f - (1f - appear) * bh * 1.6f
        slant(x, y, bw, bh, bh * 0.45f)
        fill.shader = null
        fill.color = Color.argb((140 * appear).toInt(), 0, 0, 0)
        canvas.save()
        canvas.translate(bh * 0.08f, bh * 0.1f)
        canvas.drawPath(path, fill)
        canvas.restore()
        fill.shader = LinearGradient(0f, y, 0f, y + bh, Color.rgb(255, 230, 80),
            Color.rgb(247, 181, 0), Shader.TileMode.CLAMP)
        canvas.drawPath(path, fill)
        fill.shader = null
        rim.color = Color.rgb(20, 16, 10)
        rim.strokeWidth = bh * 0.07f
        canvas.drawPath(path, rim)
        text.color = Color.rgb(24, 18, 12)
        text.textSize = fit(title, bh * 0.56f, bw * 0.86f)
        canvas.drawText(title, w / 2f, y + bh / 2f - (text.descent() + text.ascent()) / 2f, text)
    }

    private fun drawCard(canvas: Canvas, entry: Entry, cw: Float, ch: Float, f: Float, pulse: Float,
                         appear: Float) {
        val skew = ch * 0.1f
        val top = -ch / 2f
        val alpha = (255 * appear).toInt()
        slant(-cw / 2f, top, cw, ch, skew)

        fill.shader = null
        fill.color = Color.argb((130 * appear).toInt(), 0, 0, 0)
        canvas.save()
        canvas.translate(cw * 0.045f, cw * 0.055f)
        canvas.drawPath(path, fill)
        canvas.restore()

        canvas.save()
        canvas.clipPath(path)
        fill.shader = LinearGradient(0f, top, 0f, top + ch, Color.rgb(52, 54, 68),
            Color.rgb(18, 19, 26), Shader.TileMode.CLAMP)
        fill.alpha = alpha
        canvas.drawRect(-cw, top, cw, top + ch, fill)

        // The portrait over a red ground, as on the select screen's player cards
        val art = top + ch * 0.64f
        fill.shader = LinearGradient(0f, top, 0f, art, mix(Color.rgb(150, 14, 34), Color.rgb(232, 0, 43), f),
            Color.rgb(96, 4, 20), Shader.TileMode.CLAMP)
        fill.alpha = alpha
        canvas.drawRect(-cw, top, cw, art, fill)
        fill.shader = null
        val portrait = entry.portrait
        if (portrait != null) {
            // Cropped to fill the area, its top kept: the face sits there
            val areaW = cw + skew
            val areaH = art - top
            val k = max(areaW / portrait.width, areaH / portrait.height)
            val pw = portrait.width * k
            box.set(-pw / 2f, top, pw / 2f, top + portrait.height * k)
            image.alpha = alpha
            canvas.drawBitmap(portrait, null, box, image)
        } else {
            text.color = Color.argb(alpha, 255, 255, 255)
            text.textSize = ch * 0.38f
            canvas.drawText("?", 0f, (top + art) / 2f - (text.descent() + text.ascent()) / 2f, text)
        }

        // The nickname on a black band under it
        fill.color = Color.argb(alpha, 10, 10, 14)
        canvas.drawRect(-cw, art, cw, top + ch * 0.83f, fill)
        fill.color = mix(Color.rgb(120, 10, 28), Color.rgb(255, 212, 0), f)
        fill.alpha = alpha
        canvas.drawRect(-cw, art, cw, art + ch * 0.012f, fill)
        drawOutlined(canvas, entry.name, 0f, (art + top + ch * 0.83f) / 2f,
            fit(entry.name, ch * 0.105f, cw * 0.82f), Color.WHITE, alpha)

        // The level the way the select screen shows a CPU's: "Lv." on a dark bar, the number boxed
        val level = entry.level
        if (level != null) {
            val barTop = top + ch * 0.855f
            val barH = ch * 0.1f
            val barL = -cw * 0.4f
            val barR = cw * 0.4f - skew * 0.6f
            fill.color = Color.argb(alpha, 58, 58, 62)
            canvas.drawRect(barL, barTop, barR, barTop + barH, fill)
            text.textAlign = Paint.Align.LEFT
            text.color = Color.argb(alpha, 235, 235, 235)
            text.textSize = barH * 0.62f
            val mid = barTop + barH / 2f - (text.descent() + text.ascent()) / 2f
            canvas.drawText(levelLabel, barL + barH * 0.3f, mid, text)
            val boxL = barL + (barR - barL) * 0.52f
            fill.color = Color.argb(alpha, 245, 245, 245)
            canvas.drawRect(boxL, barTop + barH * 0.12f, barR - barH * 0.12f, barTop + barH * 0.88f, fill)
            text.textAlign = Paint.Align.CENTER
            text.color = Color.argb(alpha, 230, 0, 18)
            text.textSize = barH * 0.78f
            canvas.drawText(level.toString(), (boxL + barR - barH * 0.12f) / 2f,
                barTop + barH / 2f - (text.descent() + text.ascent()) / 2f, text)
        }
        canvas.restore()

        rim.color = mix(Color.rgb(78, 80, 96), Color.rgb(255, 212, 0), f)
        rim.alpha = (alpha * (1f - f * 0.25f * pulse)).toInt()
        rim.strokeWidth = cw * (0.018f + 0.03f * f)
        canvas.drawPath(path, rim)
    }

    private fun drawHints(canvas: Canvas, w: Float, h: Float, appear: Float) {
        val size = h * 0.04f
        val y = h * 0.9f
        val alpha = (255 * appear).toInt()
        text.textSize = size
        val pickW = size * 1.4f + text.measureText(pickLabel)
        val backW = size * 1.4f + text.measureText(backLabel)
        var x = (w - pickW - backW - size * 1.6f) / 2f
        for ((button, label) in listOf("A" to pickLabel, "B" to backLabel)) {
            fill.shader = null
            fill.color = Color.argb(alpha, 245, 245, 245)
            canvas.drawCircle(x + size * 0.5f, y, size * 0.55f, fill)
            text.color = Color.argb(alpha, 20, 20, 24)
            text.textSize = size * 0.7f
            canvas.drawText(button, x + size * 0.5f, y - (text.descent() + text.ascent()) / 2f, text)
            text.textSize = size
            text.textAlign = Paint.Align.LEFT
            drawOutlined(canvas, label, x + size * 1.3f, y, size, Color.WHITE, alpha, center = false)
            text.textAlign = Paint.Align.CENTER
            x += size * 1.4f + text.measureText(label) + size * 1.6f
        }
    }

    private fun drawOutlined(canvas: Canvas, s: String, x: Float, y: Float, size: Float, color: Int,
                             alpha: Int, center: Boolean = true) {
        text.textSize = size
        outline.textSize = size
        outline.textAlign = if (center) Paint.Align.CENTER else Paint.Align.LEFT
        outline.strokeWidth = size * 0.16f
        outline.alpha = alpha
        val base = y - (text.descent() + text.ascent()) / 2f
        canvas.drawText(s, x, base, outline)
        text.color = color
        text.alpha = alpha
        canvas.drawText(s, x, base, text)
    }

    /** The text size at most size that fits s in width. */
    private fun fit(s: String, size: Float, width: Float): Float {
        text.textSize = size
        val measured = text.measureText(s)
        return if (measured > width) size * width / measured else size
    }

    /** A parallelogram leaning right, as Smash's cards and banners do. */
    private fun slant(x: Float, y: Float, w: Float, h: Float, skew: Float) {
        path.reset()
        path.moveTo(x + skew / 2f, y)
        path.lineTo(x + w + skew / 2f, y)
        path.lineTo(x + w - skew / 2f, y + h)
        path.lineTo(x - skew / 2f, y + h)
        path.close()
    }

    private fun easeOut(t: Float): Float {
        val u = 1f - t.coerceIn(0f, 1f)
        return 1f - u * u * u
    }

    private fun mix(from: Int, to: Int, t: Float): Int = Color.rgb(
        (Color.red(from) + (Color.red(to) - Color.red(from)) * t).toInt(),
        (Color.green(from) + (Color.green(to) - Color.green(from)) * t).toInt(),
        (Color.blue(from) + (Color.blue(to) - Color.blue(from)) * t).toInt()
    )
}
